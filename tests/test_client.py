"""Tests for Claude SDK client functionality."""

import os
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, query
from claude_agent_sdk.types import TextBlock


class TestQueryFunction:
    """Test the main query function."""

    def test_query_single_prompt(self):
        """Test query with a single prompt."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:
                # Mock the async generator
                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="4")], model="claude-opus-4-1-20250805"
                    )

                mock_process.return_value = mock_generator()

                messages = []
                async for msg in query(prompt="What is 2+2?"):
                    messages.append(msg)

                assert len(messages) == 1
                assert isinstance(messages[0], AssistantMessage)
                assert messages[0].content[0].text == "4"

        anyio.run(_test)

    def test_query_with_options(self):
        """Test query with various options."""

        async def _test():
            with patch(
                "claude_agent_sdk._internal.client.InternalClient.process_query"
            ) as mock_process:

                async def mock_generator():
                    yield AssistantMessage(
                        content=[TextBlock(text="Hello!")],
                        model="claude-opus-4-1-20250805",
                    )

                mock_process.return_value = mock_generator()

                options = ClaudeAgentOptions(
                    allowed_tools=["Read", "Write"],
                    system_prompt="You are helpful",
                    permission_mode="acceptEdits",
                    max_turns=5,
                )

                messages = []
                async for msg in query(prompt="Hi", options=options):
                    messages.append(msg)

                # Verify process_query was called with correct prompt and options
                mock_process.assert_called_once()
                call_args = mock_process.call_args
                assert call_args[1]["prompt"] == "Hi"
                assert call_args[1]["options"] == options

        anyio.run(_test)

    def test_query_with_cwd(self):
        """Test query with custom working directory."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch(
                    "claude_agent_sdk._internal.query.Query.initialize",
                    new_callable=AsyncMock,
                ),
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport

                # Mock the message stream
                async def mock_receive():
                    yield {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Done"}],
                            "model": "claude-opus-4-1-20250805",
                        },
                    }
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 1000,
                        "duration_api_ms": 800,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test-session",
                        "total_cost_usd": 0.001,
                    }

                mock_transport.read_messages = mock_receive
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                options = ClaudeAgentOptions(cwd="/custom/path")
                messages = []
                async for msg in query(prompt="test", options=options):
                    messages.append(msg)

                # Verify transport was created with correct parameters
                mock_transport_class.assert_called_once()
                call_kwargs = mock_transport_class.call_args.kwargs
                assert call_kwargs["prompt"] == "test"
                assert call_kwargs["options"].cwd == "/custom/path"

        anyio.run(_test)

    def _run_query_with_mocked_internals(
        self, env_patch, expected_timeout, options=None
    ):
        """Helper: run query() with mocked internals; check initialize_timeout and
        return the kwargs given to Query."""
        query_kwargs: dict = {}

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch("claude_agent_sdk._internal.client.Query") as mock_query_class,
                patch.dict(os.environ, env_patch, clear=False),
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                mock_query = AsyncMock()
                mock_query_class.return_value = mock_query
                mock_query.start = AsyncMock()
                mock_query.initialize = AsyncMock()
                mock_query.close = AsyncMock()
                mock_query.close_receive_stream = Mock()
                mock_query._tg = None

                def _consume_coro(coro):
                    coro.close()
                    return Mock()

                mock_query.spawn_task = Mock(side_effect=_consume_coro)

                async def mock_receive():
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 100,
                        "duration_api_ms": 80,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test",
                    }

                mock_query.receive_messages = mock_receive

                async for _ in query(
                    prompt="test", options=options or ClaudeAgentOptions()
                ):
                    pass

                call_kwargs = mock_query_class.call_args.kwargs
                assert call_kwargs["initialize_timeout"] == expected_timeout
                query_kwargs.update(call_kwargs)

        anyio.run(_test)
        return query_kwargs

    def test_query_passes_initialize_timeout_from_env(self):
        """Test that query() reads CLAUDE_CODE_STREAM_CLOSE_TIMEOUT and passes it to Query."""
        self._run_query_with_mocked_internals(
            env_patch={"CLAUDE_CODE_STREAM_CLOSE_TIMEOUT": "120000"},
            expected_timeout=120.0,
        )

    def test_query_uses_default_initialize_timeout(self):
        """Test that query() defaults to 60s initialize timeout when env var is not set."""
        # Ensure env var is absent for this test
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", None)
            self._run_query_with_mocked_internals(
                env_patch={},
                expected_timeout=60.0,
            )

    @pytest.mark.parametrize(
        "system_prompt, expected",
        [
            ({"type": "custom", "prompt": "Be helpful", "snapshot": False}, False),
            ({"type": "preset", "preset": "claude_code", "snapshot": True}, True),
            ({"type": "preset", "preset": "claude_code"}, None),
            ({"type": "file", "path": "/p.md", "snapshot": False}, None),
            ("Be helpful", None),
        ],
    )
    def test_query_passes_system_prompt_snapshot(self, system_prompt, expected):
        """query() hands snapshot to Query only for the preset and custom forms."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", None)
            call_kwargs = self._run_query_with_mocked_internals(
                env_patch={},
                expected_timeout=60.0,
                options=ClaudeAgentOptions(system_prompt=system_prompt),
            )
        assert call_kwargs["system_prompt_snapshot"] is expected

    def test_string_prompt_spawns_wait_for_result_as_task(self):
        """Test that string prompts spawn wait_for_result_and_end_input as a background
        task instead of awaiting it inline, preventing deadlock when the message
        buffer fills up (e.g. >50 tool calls with hooks)."""

        async def _test():
            with (
                patch(
                    "claude_agent_sdk._internal.client.SubprocessCLITransport"
                ) as mock_transport_class,
                patch("claude_agent_sdk._internal.client.Query") as mock_query_class,
            ):
                mock_transport = AsyncMock()
                mock_transport_class.return_value = mock_transport
                mock_transport.connect = AsyncMock()
                mock_transport.close = AsyncMock()
                mock_transport.end_input = AsyncMock()
                mock_transport.write = AsyncMock()
                mock_transport.is_ready = Mock(return_value=True)

                mock_query = AsyncMock()
                mock_query_class.return_value = mock_query
                mock_query.start = AsyncMock()
                mock_query.initialize = AsyncMock()
                mock_query.close = AsyncMock()
                mock_query.close_receive_stream = Mock()
                mock_query._tg = None

                def _consume_coro(coro):
                    coro.close()
                    return Mock()

                mock_query.spawn_task = Mock(side_effect=_consume_coro)

                async def mock_receive():
                    yield {
                        "type": "result",
                        "subtype": "success",
                        "duration_ms": 100,
                        "duration_api_ms": 80,
                        "is_error": False,
                        "num_turns": 1,
                        "session_id": "test",
                    }

                mock_query.receive_messages = mock_receive

                async for _ in query(prompt="test", options=ClaudeAgentOptions()):
                    pass

                mock_query.spawn_task.assert_called_once()
                assert not mock_query.wait_for_result_and_end_input.await_args_list, (
                    "wait_for_result_and_end_input should be spawned as a task, "
                    "not awaited directly"
                )

        anyio.run(_test)


class TestClaudeSDKClientTrioBackend:
    """Regression test: ClaudeSDKClient must work under trio.

    ``Query.start``/``spawn_task`` must not call ``asyncio.get_running_loop()``
    (raises ``RuntimeError: no running event loop`` under trio). This test
    drives connect()/disconnect() end-to-end on the trio backend with a mock
    transport that uses only anyio primitives.
    """

    def test_client_connect_under_trio(self):
        import json

        from claude_agent_sdk import ClaudeSDKClient

        def _make_trio_safe_transport():
            """Mock transport using anyio.sleep so it runs under trio."""
            mock_transport = AsyncMock()
            mock_transport.connect = AsyncMock()
            mock_transport.close = AsyncMock()
            mock_transport.end_input = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            written: list[str] = []

            async def mock_write(data):
                written.append(data)

            mock_transport.write = AsyncMock(side_effect=mock_write)

            async def read_messages():
                # Respond to the initialize control_request so connect()
                # doesn't block on the 60s timeout.
                for _ in range(200):
                    for msg_str in written:
                        try:
                            msg = json.loads(msg_str.strip())
                        except (json.JSONDecodeError, AttributeError):
                            continue
                        if (
                            msg.get("type") == "control_request"
                            and msg.get("request", {}).get("subtype") == "initialize"
                        ):
                            yield {
                                "type": "control_response",
                                "response": {
                                    "request_id": msg.get("request_id"),
                                    "subtype": "success",
                                    "response": {},
                                },
                            }
                            return
                    await anyio.sleep(0.01)

            mock_transport.read_messages = read_messages
            return mock_transport

        async def _test():
            mock_transport = _make_trio_safe_transport()
            async with ClaudeSDKClient(transport=mock_transport) as client:
                assert client._transport is mock_transport
                mock_transport.connect.assert_called_once()
            mock_transport.close.assert_called_once()

        anyio.run(_test, backend="trio")


class TestClaudeSDKClientSystemPromptSnapshot:
    """ClaudeSDKClient.connect() sends the system prompt's snapshot on initialize."""

    @staticmethod
    def _initialize_request_sent_by_client(options):
        import json

        from claude_agent_sdk import ClaudeSDKClient

        written: list[str] = []
        mock_transport = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)
        mock_transport.write = AsyncMock(side_effect=written.append)

        def initialize_requests():
            messages = [json.loads(data) for data in written]
            return [
                m
                for m in messages
                if m.get("type") == "control_request"
                and m["request"]["subtype"] == "initialize"
            ]

        async def read_messages():
            for _ in range(200):
                for msg in initialize_requests():
                    yield {
                        "type": "control_response",
                        "response": {
                            "request_id": msg["request_id"],
                            "subtype": "success",
                            "response": {},
                        },
                    }
                    return
                await anyio.sleep(0.01)

        mock_transport.read_messages = read_messages

        async def _connect():
            async with ClaudeSDKClient(options=options, transport=mock_transport):
                pass

        anyio.run(_connect)
        (sent,) = initialize_requests()
        return sent["request"]

    @pytest.mark.parametrize(
        "system_prompt, expected",
        [
            ({"type": "custom", "prompt": "Be helpful", "snapshot": False}, False),
            ({"type": "preset", "preset": "claude_code", "snapshot": True}, True),
        ],
    )
    def test_connect_sends_snapshot(self, system_prompt, expected):
        request = self._initialize_request_sent_by_client(
            ClaudeAgentOptions(system_prompt=system_prompt)
        )
        assert request["systemPromptSnapshot"] is expected

    def test_connect_omits_snapshot_when_unset(self):
        request = self._initialize_request_sent_by_client(
            ClaudeAgentOptions(
                system_prompt={"type": "preset", "preset": "claude_code"}
            )
        )
        assert "systemPromptSnapshot" not in request


class TestClaudeSDKClientResourceCleanup:
    """Regression for #859: disconnect() must close the receive stream.

    ``Query.close()`` deliberately leaves ``_message_receive`` open so a
    concurrently-draining consumer can finish (see
    ``test_buffered_messages_drain_after_close_*``). The owning consumer
    is responsible for closing it once done; ``disconnect()`` is that
    consumer for ``ClaudeSDKClient``. Before the fix, ``__aexit__`` left
    the stream open and anyio's ``__del__`` emitted ``ResourceWarning:
    Unclosed <MemoryObjectReceiveStream>`` under PYTHONDEVMODE.
    """

    def test_disconnect_closes_receive_stream(self):
        from anyio import ClosedResourceError

        from claude_agent_sdk import ClaudeSDKClient
        from claude_agent_sdk._internal.query import Query

        async def _test():
            mock_transport = AsyncMock()
            mock_transport.is_ready = Mock(return_value=True)

            async def read_messages():
                return
                yield

            mock_transport.read_messages = read_messages

            client = ClaudeSDKClient(transport=mock_transport)
            client._transport = mock_transport
            client._query = Query(transport=mock_transport, is_streaming_mode=True)
            await client._query.start()
            receive_stream = client._query._message_receive
            await client.disconnect()
            with pytest.raises(ClosedResourceError):
                receive_stream.receive_nowait()

        anyio.run(_test)

    @staticmethod
    async def _client_with_buffered(*texts, result=False):
        """Connected client whose message stream already holds these messages."""
        from claude_agent_sdk import ClaudeSDKClient
        from claude_agent_sdk._internal.query import Query

        mock_transport = AsyncMock()
        mock_transport.is_ready = Mock(return_value=True)

        async def blocking_read():
            await anyio.Event().wait()  # never set
            yield {}  # pragma: no cover - unreachable

        mock_transport.read_messages = blocking_read

        client = ClaudeSDKClient(transport=mock_transport)
        client._transport = mock_transport
        query = Query(transport=mock_transport, is_streaming_mode=True)
        client._query = query
        await query.start()
        for text in texts:
            query._message_send.send_nowait(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": "m",
                        "content": [{"type": "text", "text": text}],
                    },
                    "parent_tool_use_id": None,
                }
            )
        if result:
            query._message_send.send_nowait(
                {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 1,
                    "duration_api_ms": 1,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "s",
                }
            )
        return client, query

    @pytest.mark.parametrize("backend", ["asyncio", "trio"])
    def test_disconnect_lets_busy_reader_drain_buffer(self, backend):
        """A reader in user code when another task disconnects keeps its messages.

        Closing the receive side in disconnect() made the reader's next
        receive_nowait() raise ClosedResourceError with messages still
        buffered. The stream must close once that reader finishes instead.
        """

        async def _test():
            with anyio.fail_after(5.0):
                client, query = await self._client_with_buffered("0", "1", "2")
                received = []
                reader_errors: list[BaseException] = []
                got_first = anyio.Event()
                in_user_code = anyio.Event()

                async def reader():
                    try:
                        async for msg in client.receive_messages():
                            received.append(msg)
                            if len(received) == 1:
                                got_first.set()
                                await in_user_code.wait()
                    except BaseException as e:  # noqa: BLE001
                        reader_errors.append(e)

                async with anyio.create_task_group() as tg:
                    tg.start_soon(reader)
                    await got_first.wait()
                    await client.disconnect()
                    in_user_code.set()

                assert reader_errors == []
                assert [m.content[0].text for m in received] == ["0", "1", "2"]
                with pytest.raises(anyio.ClosedResourceError):
                    query._message_receive.receive_nowait()

        anyio.run(_test, backend=backend)

    @pytest.mark.parametrize("backend", ["asyncio", "trio"])
    def test_disconnect_closes_stream_after_receive_response(self, backend):
        """receive_response() ends its iteration itself, so nothing waits on GC."""

        async def _test():
            client, query = await self._client_with_buffered("0", result=True)
            received = [msg async for msg in client.receive_response()]
            assert len(received) == 2
            await client.disconnect()
            with pytest.raises(anyio.ClosedResourceError):
                query._message_receive.receive_nowait()

        anyio.run(_test, backend=backend)

    @pytest.mark.parametrize("backend", ["asyncio", "trio"])
    def test_disconnect_waits_for_every_active_reader(self, backend):
        """With two readers, the stream stays open until the second one ends."""
        from contextlib import aclosing

        async def _test():
            with anyio.fail_after(5.0):
                client, query = await self._client_with_buffered("0", "1", "2")
                both_reading = [anyio.Event(), anyio.Event()]
                release = [anyio.Event(), anyio.Event()]
                second_got: list[str] = []

                async def first():
                    async with aclosing(client.receive_messages()) as messages:
                        async for _ in messages:
                            both_reading[0].set()
                            await release[0].wait()
                            break

                async def second():
                    async for msg in client.receive_messages():
                        if not both_reading[1].is_set():
                            both_reading[1].set()
                            await release[1].wait()
                        else:
                            second_got.append(msg.content[0].text)

                async with anyio.create_task_group() as tg:
                    tg.start_soon(first)
                    await both_reading[0].wait()
                    tg.start_soon(second)
                    await both_reading[1].wait()
                    await client.disconnect()
                    release[0].set()
                    await anyio.sleep(0.05)
                    # First reader is done; the second is still iterating.
                    assert query._receive_close_pending
                    assert query._active_receivers == 1
                    release[1].set()

                assert second_got == ["2"]
                with pytest.raises(anyio.ClosedResourceError):
                    query._message_receive.receive_nowait()

        anyio.run(_test, backend=backend)

    @pytest.mark.parametrize("backend", ["asyncio", "trio"])
    def test_disconnect_with_abandoned_reader_closes_on_aclose(self, backend):
        """An iteration left suspended by break keeps the close pending until closed."""

        async def _test():
            client, query = await self._client_with_buffered("0", "1")
            messages = client.receive_messages()
            async for _ in messages:
                break
            await client.disconnect()
            assert query._receive_close_pending
            await messages.aclose()
            with pytest.raises(anyio.ClosedResourceError):
                query._message_receive.receive_nowait()

        anyio.run(_test, backend=backend)
