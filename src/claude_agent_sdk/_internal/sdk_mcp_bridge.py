"""Bridge between the CLI's ``mcp_message`` control requests and an in-process MCP server.

The CLI speaks JSON-RPC to SDK MCP servers through the control channel: each
message arrives as a raw dict and expects (for requests) a raw dict back. On
this side the server is a real ``mcp.server.Server``, which serves a
connection over a pair of streams. ``SdkMcpBridge`` connects the two using
mcp's own in-memory transport, so every method the server implements
(initialize, tools, resources, prompts, ping, ...) is dispatched by mcp itself
rather than re-implemented here.

One bridge exists per configured SDK server for the lifetime of a ``Query``
and runs one *session*: one ``Server.run`` over one stream pair, started by
the first message and closed with the ``Query``. Every message flows into
that session, including a repeated handshake (the CLI re-initializes its SDK
servers in some situations, and mcp accepts that on a live connection). A
session is over when ``Server.run`` returns; if that happens underneath the
CLI (the server failed), later messages are answered with an error naming
the server and the cause. Only a new ``initialize`` starts the server again,
and the CLI sends one only for a server whose handshake failed (it retries
those every turn); a server that dies after a good handshake stays down for
the rest of the query, as with the TypeScript SDK.
"""

from __future__ import annotations

import logging
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import anyio
import anyio.abc
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage

from ._mcp_compat import can_cancel_requests, dump_jsonrpc, parse_jsonrpc
from ._task_compat import TaskHandle, spawn_detached

if TYPE_CHECKING:
    from types import TracebackType

    from mcp.server import Server as McpServer

logger = logging.getLogger(__name__)

# How long ending a session waits for the server to wind down after it has
# been told to stop. Servers stop within milliseconds; only a tool that does
# not react to cancellation (one blocked in a thread, say) can hold one open,
# and that is not worth hanging a reconnect or shutdown on.
SHUTDOWN_GRACE_SECONDS = 5.0

# JSON-RPC "method not found": what a client answers for a request it does
# not support.
_METHOD_NOT_FOUND = -32601


class _SendStream(Protocol):
    """The part of the client-side write stream the session uses (its concrete
    type differs between mcp majors)."""

    async def send(self, item: SessionMessage, /) -> None: ...
    async def aclose(self) -> None: ...


class _PendingResponse:
    """The slot a request's response is delivered to.

    It lives in the session's table of unanswered requests until a response
    arrives or the session fails it. Ids are unique among unanswered
    requests; a request that reuses one is refused before it reaches the
    server, so a response can only ever belong to the request that
    registered the id.
    """

    def __init__(
        self, table: dict[Any, _PendingResponse], request_id: Any, method: Any
    ) -> None:
        if request_id in table:
            raise Exception(f"Request id {request_id!r} is already in flight")
        self.method = method
        self._table = table
        self._request_id = request_id
        self._event = anyio.Event()
        self._outcome: dict[str, Any] | Exception | None = None
        table[request_id] = self

    def resolve(self, outcome: dict[str, Any] | Exception) -> None:
        if self._outcome is None:
            self._outcome = outcome
            self._event.set()
        if self._table.get(self._request_id) is self:
            del self._table[self._request_id]

    async def wait(self) -> dict[str, Any]:
        # The slot stays in the table even if this waiter is cancelled: the
        # server still owns the request, so its id must stay reserved (a new
        # request reusing it would otherwise receive this one's response) and
        # the session must still be able to cancel it when it closes.
        await self._event.wait()
        assert self._outcome is not None
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@dataclass(slots=True)
class _SendAttempt:
    """Tracks one client-to-server send across async cancellation."""

    message: SessionMessage
    seq: int
    delivered: bool = False
    aborted: bool = False


@dataclass(slots=True)
class _InboundState:
    """Shared delivery tracking between ``_Session.submit`` and ``_InboundReceiveStream``."""

    in_transit: dict[int, _SendAttempt] = field(default_factory=dict)
    aborted: deque[tuple[int, int]] = field(default_factory=deque)
    recv_count: int = 0


class _InboundReceiveStream:
    """Wraps the server's inbound stream to observe real consumption.

    ``anyio`` moves a blocked item from waiting senders into the stream buffer
    right before waking the waiting sender task. If that sender is cancelled in
    the gap before resuming, ``send()`` still raises cancellation even though
    the item entered the buffer. Wrapping ``receive()`` records actual
    consumption and skips any item aborted before the server read it.
    """

    def __init__(self, inner: Any, state: _InboundState) -> None:
        self._inner = inner
        self._state = state

    def _sweep_stale_aborted(self, received_seq: int) -> None:
        while self._state.aborted:
            sweep_at_seq, msg_id = self._state.aborted[0]
            if received_seq < sweep_at_seq:
                break
            self._state.aborted.popleft()
            self._state.in_transit.pop(msg_id, None)

    def _consume(self, item: Any) -> bool:
        """Record consumption and return True if an aborted item must be skipped."""
        self._state.recv_count += 1
        entry = self._state.in_transit.pop(id(item), None)
        if entry is None:
            return False
        if self._state.aborted:
            self._sweep_stale_aborted(entry.seq)
        if entry.aborted:
            if self._state.aborted and self._state.aborted[0][1] == id(item):
                self._state.aborted.popleft()
            return True
        entry.delivered = True
        return False

    async def receive(self) -> Any:
        while True:
            item = await self._inner.receive()
            if not self._consume(item):
                return item

    def receive_nowait(self) -> Any:
        while True:
            item = self._inner.receive_nowait()
            if not self._consume(item):
                return item

    def statistics(self) -> Any:
        return self._inner.statistics()

    def close(self) -> None:
        self._inner.close()

    async def aclose(self) -> None:
        await self._inner.aclose()

    def clone(self) -> _InboundReceiveStream:
        return _InboundReceiveStream(self._inner.clone(), self._state)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __enter__(self) -> _InboundReceiveStream:
        self._inner.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Any:
        return self._inner.__exit__(exc_type, exc_val, exc_tb)

    def __aiter__(self) -> _InboundReceiveStream:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> _InboundReceiveStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()


class _Session:
    """One ``Server.run`` over one in-memory stream pair.

    Runs as a single detached task that owns the streams, the server, and the
    only reader of the server's output, and ends when ``Server.run`` returns.
    Responses are matched to waiting requests by JSON-RPC id. Nothing carries
    server-initiated traffic to the CLI yet: notifications (logging, progress,
    list_changed) are dropped, and requests (roots, sampling, elicitation) are
    refused so the server's caller fails at once instead of waiting forever.
    """

    def __init__(self, name: str, server: McpServer) -> None:
        self._name = name
        self._server = server
        self.failure: BaseException | None = None
        self._pending: dict[Any, _PendingResponse] = {}
        self._inbound_state = _InboundState()
        self._send_lock = anyio.Lock()
        self._next_send_seq = 0
        self._to_server: _SendStream | None = None
        self._tasks: anyio.abc.TaskGroup | None = None
        self._ready = anyio.Event()
        self._output_ended = False
        self._task: TaskHandle = spawn_detached(self._run())
        # A task that finishes without ever running (cancelled before its
        # first step) never reaches the code that marks the session ready;
        # finishing must count as ready too or waiting on it would never end.
        self._task.add_done_callback(lambda _task: self._ready.set())

    @property
    def finished(self) -> bool:
        """True once nothing sent to this session can be answered any more.

        That is so from the moment the server's output ends, which can be a
        little before the task itself is done (a lifespan finishing its
        teardown, the grace period running out on a stuck tool).
        """
        return self._output_ended or self._task.done()

    async def _run(self) -> None:
        try:
            async with create_client_server_memory_streams() as (client, server):
                from_server, self._to_server = client
                server_read, server_write = server
                inbound = _InboundReceiveStream(server_read, self._inbound_state)
                served = anyio.Event()
                async with anyio.create_task_group() as tg:
                    self._tasks = tg
                    tg.start_soon(self._serve, inbound, server_write, served)
                    self._ready.set()
                    async for item in from_server:
                        await self._deliver(item)
                    # The server has closed its output, which it does as it
                    # shuts down, so a request still unanswered never will be.
                    self._output_ended = True
                    self._fail_pending("session closed")
                    # Let run() return on its own so a lifespan gets to finish
                    # its teardown. mcp 1.27+ and 2.x cancel running tools at
                    # this point and return at once; older releases let them
                    # run, so there a tool still going holds the session open
                    # until it finishes or the grace period ends.
                    with anyio.move_on_after(SHUTDOWN_GRACE_SECONDS):
                        await served.wait()
                    tg.cancel_scope.cancel()
        except Exception as e:
            # A failed task group wraps the server's exception; name that one.
            cause: BaseException = e
            grouped = getattr(cause, "exceptions", None)
            while grouped is not None and len(grouped) == 1:
                cause = grouped[0]
                grouped = getattr(cause, "exceptions", None)
            self.failure = cause
            logger.exception("SDK MCP server %r failed", self._name)
        finally:
            self._ready.set()
            self._fail_pending("session closed")
            self._inbound_state.in_transit.clear()
            self._inbound_state.aborted.clear()

    def _fail_pending(self, reason: str) -> None:
        failure = Exception(f"SDK MCP server {self._name!r} {reason}")
        for pending in list(self._pending.values()):
            pending.resolve(failure)

    async def _serve(
        self, server_read: Any, server_write: Any, served: anyio.Event
    ) -> None:
        try:
            await self._server.run(
                server_read,
                server_write,
                self._server.create_initialization_options(),
                raise_exceptions=False,
            )
        finally:
            served.set()
            # Not every mcp version closes the server's output when run()
            # returns; closing it here lets the reader drain what is left and
            # finish.
            await server_write.aclose()

    async def _deliver(self, item: SessionMessage | Exception) -> None:
        if isinstance(item, Exception):
            logger.debug("SDK MCP server %r sent an error: %r", self._name, item)
            return
        payload = dump_jsonrpc(item)
        if "method" not in payload:
            self._respond(payload)
        elif "id" in payload:
            # A request from the server to the client. The reader must never
            # wait on the server it drains, so the answer is sent from a task.
            assert self._tasks is not None
            self._tasks.start_soon(self._answer_server_request, payload)
        else:
            logger.debug(
                "Dropping %r notification from SDK MCP server %r",
                payload["method"],
                self._name,
            )

    async def _answer_server_request(self, request: dict[str, Any]) -> None:
        """Answer a request the server sent to the client.

        A ping is answered here. Nothing carries other requests (roots,
        sampling, elicitation) to the CLI yet, so they are refused with
        "method not found", which is what a client that does not support them
        answers, and the server's caller fails at once instead of waiting.
        """
        method = request["method"]
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "ping":
            answer["result"] = {}
        else:
            logger.warning(
                "SDK MCP server %r sent a %r request to the client; requests to "
                "the client are not supported for SDK servers yet, refusing it",
                self._name,
                method,
            )
            answer["error"] = {
                "code": _METHOD_NOT_FOUND,
                "message": f"{method} is not supported for SDK servers",
            }
        message, _ = parse_jsonrpc(answer, self._respond)
        assert self._to_server is not None
        # A closed stream means the session is shutting down and the server's
        # caller is being cancelled anyway.
        with suppress(anyio.ClosedResourceError, anyio.BrokenResourceError):
            await self._send_tracked(message, None)

    def _respond(self, payload: dict[str, Any]) -> None:
        pending = self._pending.get(payload.get("id"))
        if pending is None:
            logger.debug(
                "Dropping response for unknown request id %r from SDK MCP server %r",
                payload.get("id"),
                self._name,
            )
            return
        pending.resolve(payload)

    def _abort_unsent(
        self,
        attempt: _SendAttempt | None,
        recv_before: int,
        pending: _PendingResponse | None,
    ) -> None:
        if attempt is not None and attempt.delivered:
            # The server already pulled this request off the stream, so its
            # id must stay reserved until the server responds.
            return
        if pending is not None:
            pending.resolve(Exception("unsent"))
        if attempt is None:
            return
        if self._inbound_state.recv_count == recv_before:
            self._inbound_state.in_transit.pop(id(attempt.message), None)
        else:
            # A receive happened while send() was blocked: the message may have
            # entered the buffer just as send() was cancelled, so keep a
            # tombstone to drop it if receive() sees it next.
            attempt.aborted = True
            self._inbound_state.aborted.append(
                (self._next_send_seq, id(attempt.message))
            )

    async def _send_tracked(
        self,
        parsed: SessionMessage,
        pending: _PendingResponse | None,
    ) -> None:
        # Serialize sends to the 1-slot stream so extra callers wait before
        # entering in_transit and cannot leave tombstones after a batch cancel,
        # and so every message sent to the server carries a monotonic seq that
        # sweeps any prior checkpoint-aborted tombstone on next receive.
        attempt: _SendAttempt | None = None
        recv_before = 0
        try:
            async with self._send_lock:
                seq = self._next_send_seq
                self._next_send_seq += 1
                attempt = _SendAttempt(message=parsed, seq=seq)
                self._inbound_state.in_transit[id(parsed)] = attempt
                recv_before = self._inbound_state.recv_count
                assert self._to_server is not None
                await self._to_server.send(parsed)
        except BaseException:
            self._abort_unsent(attempt, recv_before, pending)
            raise

    async def submit(self, message: dict[str, Any]) -> _PendingResponse | None:
        """Forward one JSON-RPC message to the server.

        Returns where the response will arrive if mcp read the message as a
        request, and ``None`` for notifications and responses.
        """
        await self._ready.wait()
        if self._to_server is None:
            raise Exception(f"SDK MCP server {self._name!r} is not running")
        if message.get(
            "method"
        ) == "notifications/cancelled" and not can_cancel_requests(self._server):
            logger.debug(
                "Not passing a cancellation on to SDK MCP server %r: its mcp "
                "version cannot cancel a running handler safely",
                self._name,
            )
            return None
        parsed, request_id = parse_jsonrpc(message, self._respond)
        pending = None
        if request_id is not None:
            pending = _PendingResponse(self._pending, request_id, message.get("method"))
        try:
            await self._send_tracked(parsed, pending)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The session went away between being chosen and this send.
            raise Exception(f"SDK MCP server {self._name!r} session closed") from None
        return pending

    async def aclose(self) -> None:
        """End the session and wait (boundedly) for it to wind down.

        Closing the inbound stream is the stop signal: the server fails or
        cancels what it has in flight, ``run`` returns and the task ends. A
        tool that ignores cancellation can hold that up; past the grace
        period the task is cancelled and left to finish when that code lets
        go, rather than blocking the caller on it.
        """
        await self._ready.wait()
        deadline = anyio.current_time() + SHUTDOWN_GRACE_SECONDS
        if self._to_server is not None:
            # Cancel whatever is still in flight through mcp's own path first.
            # mcp releases that do not cancel running handlers when the
            # connection closes (< 1.27) would otherwise either hold the
            # session open for the whole grace period or, if the tool
            # finishes inside it, answer into a stream that is already closed.
            # A server that is not reading (still starting its lifespan, say)
            # cannot take these, so sending them comes out of the grace
            # period too. The handshake itself is never cancelled.
            with anyio.CancelScope(deadline=deadline):
                if can_cancel_requests(self._server):
                    for request_id, pending in list(self._pending.items()):
                        if pending.method == "initialize":
                            continue
                        with suppress(Exception):
                            await self.submit(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "notifications/cancelled",
                                    "params": {
                                        "requestId": request_id,
                                        "reason": "connection closing",
                                    },
                                }
                            )
            await self._to_server.aclose()
        with anyio.CancelScope(deadline=deadline):
            await self._task.wait()
        if not self._task.done():
            logger.warning(
                "SDK MCP server %r did not stop within %ss of being closed "
                "(a tool is probably blocked outside the event loop); "
                "no longer waiting for it",
                self._name,
                SHUTDOWN_GRACE_SECONDS,
            )
            self._task.cancel()
            # The task may take a while to actually unwind (a handler blocked
            # in a thread keeps it alive), so do not leave callers waiting on
            # responses that can no longer arrive.
            self._fail_pending("session closed")


class SdkMcpBridge:
    """Routes raw JSON-RPC for one in-process MCP server through mcp's memory transport."""

    def __init__(self, name: str, server: McpServer) -> None:
        self.name = name
        self._server = server
        self._session: _Session | None = None
        self._closed = False

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one message from the CLI.

        Returns the JSON-RPC response dict for requests and ``None`` for
        notifications and responses, which expect no reply.
        """
        if self._closed:
            raise Exception(f"SDK MCP server {self.name!r} is closed")
        session, stopped = self._session, None
        if session is None:
            session = self._session = _Session(self.name, self._server)
        elif session.finished:
            # The server stopped underneath the CLI. It stays stopped, with
            # every message answered by an error saying so, until the CLI
            # starts it again with a new handshake (which it does for a
            # server it considers failed).
            if message.get("method") != "initialize":
                cause = session.failure or "its run() returned"
                raise Exception(f"SDK MCP server {self.name!r} stopped: {cause}")
            stopped, session = session, _Session(self.name, self._server)
            self._session = session
            await stopped.aclose()
        pending = await session.submit(message)
        if pending is None:
            return None
        return await pending.wait()

    async def aclose(self) -> None:
        """Stop the session, if any. Safe to call more than once."""
        self._closed = True
        if self._session is not None:
            session, self._session = self._session, None
            await session.aclose()
