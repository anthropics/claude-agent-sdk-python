"""Error types for Claude SDK."""

from typing import Any


class ClaudeSDKError(Exception):
    """Base exception for all Claude SDK errors."""


class CLIConnectionError(ClaudeSDKError):
    """Raised when unable to connect to Claude Code."""


class CLINotFoundError(CLIConnectionError):
    """Raised when Claude Code is not found or not installed."""

    def __init__(
        self, message: str = "Claude Code not found", cli_path: str | None = None
    ):
        if cli_path:
            message = f"{message}: {cli_path}"
        super().__init__(message)


class ProcessError(ClaudeSDKError):
    """Raised when the CLI process fails."""

    def __init__(
        self, message: str, exit_code: int | None = None, stderr: str | None = None
    ):
        self.exit_code = exit_code
        self.stderr = stderr

        if exit_code is not None:
            message = f"{message} (exit code: {exit_code})"
        if stderr:
            message = f"{message}\nError output: {stderr}"

        super().__init__(message)


def _normalize_result_errors(raw: Any) -> list[str]:
    """Normalize the ``errors`` field of a ``result`` frame to clean strings.

    The CLI emits a list of strings; tolerate a bare string (older/buggy
    emitters) and drop non-string or blank entries so the structured
    ``ResultError.errors`` and the exception text always agree.
    """
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        return []
    return [e.strip() for e in raw if isinstance(e, str) and e.strip()]


class ResultError(ProcessError):
    """Raised when the CLI exits after reporting a terminal error result.

    The CLI ends a failed run by emitting a ``result`` message with
    ``is_error: true`` (yielded to you as a :class:`ResultMessage`) and then
    exiting non-zero. This exception replaces the bare "exit code 1"
    :class:`ProcessError` for that case and carries the result's payload, so
    callers can branch on *why* the run failed without string matching::

        try:
            async for message in query(prompt="..."):
                ...
        except ResultError as e:
            if e.terminal_reason == "api_error":   # e.g. overloaded / timeout
                retry()
            elif e.subtype == "error_max_turns":
                ...

    It subclasses :class:`ProcessError`, so existing ``except ProcessError``
    handlers keep working.

    Attributes:
        subtype: The result subtype (``"error_max_turns"``,
            ``"error_during_execution"``, ... — or ``"success"`` when the
            agent loop itself completed but the last turn was an API error).
        errors: Error strings reported by the CLI (may be empty).
        result: The result text, if any. For API failures this holds the
            ``"API Error: ..."`` prose.
        api_error_status: HTTP status of the failing API call, if any.
        terminal_reason: Why the run ended (e.g. ``"api_error"``,
            ``"max_turns"``), if reported by the CLI.
        session_id: Session the result belongs to, if reported.
        data: The raw ``result`` message payload as emitted by the CLI.
        exit_code: Inherited from :class:`ProcessError`.
    """

    def __init__(
        self,
        message: str,
        data: dict[str, Any] | None = None,
        exit_code: int | None = None,
    ):
        # `data` is optional so the default exception reconstruction protocol
        # (``type(e)(*e.args)``, used by pickle/copy and therefore by
        # multiprocessing) still works; the real attributes are restored from
        # ``__dict__`` afterwards.
        data = data if isinstance(data, dict) else {}
        self.data = data
        subtype = data.get("subtype")
        self.subtype: str | None = subtype if isinstance(subtype, str) else None
        self.errors: list[str] = _normalize_result_errors(data.get("errors"))
        result = data.get("result")
        self.result: str | None = result if isinstance(result, str) else None
        status = data.get("api_error_status")
        self.api_error_status: int | None = status if isinstance(status, int) else None
        reason = data.get("terminal_reason")
        self.terminal_reason: str | None = reason if isinstance(reason, str) else None
        session_id = data.get("session_id")
        self.session_id: str | None = (
            session_id if isinstance(session_id, str) else None
        )
        super().__init__(message, exit_code=exit_code)


class CLIJSONDecodeError(ClaudeSDKError):
    """Raised when unable to decode JSON from CLI output."""

    def __init__(self, line: str, original_error: Exception):
        self.line = line
        self.original_error = original_error
        super().__init__(f"Failed to decode JSON: {line[:100]}...")


class MessageParseError(ClaudeSDKError):
    """Raised when unable to parse a message from CLI output."""

    def __init__(self, message: str, data: dict[str, Any] | None = None):
        self.data = data
        super().__init__(message)
