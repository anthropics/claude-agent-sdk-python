"""End-to-end tests for how terminal error results surface as exceptions."""

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ProcessError,
    ResultError,
    ResultMessage,
    query,
)

# A model id the API will reject. The CLI reports the failure as a terminal
# result with is_error=True, subtype="success", an empty errors[] and the API
# error prose in `result`, then exits non-zero once stdin closes — no
# successful inference is billed, so this is cheap and deterministic.
_BAD_MODEL = "claude-nonexistent-model-for-sdk-e2e"


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_api_error_raises_result_error_with_payload():
    """query(): an API-side failure raises ResultError (a ProcessError)
    carrying the CLI's error prose and result payload, instead of a bare
    Exception saying "error result: success"."""
    options = ClaudeAgentOptions(model=_BAD_MODEL, max_turns=1)

    result: ResultMessage | None = None
    with pytest.raises(Exception) as exc_info:
        async for message in query(prompt="Reply with: pong", options=options):
            if isinstance(message, ResultMessage):
                result = message

    exc = exc_info.value
    assert result is not None, "the error result should be yielded before raising"
    assert result.is_error is True

    # Typed, and backwards compatible with `except ProcessError`.
    assert isinstance(exc, ResultError), (
        f"expected ResultError, got {type(exc).__name__}: {exc}"
    )
    assert isinstance(exc, ProcessError)
    assert exc.exit_code is not None

    # Text is the real cause, never the "success" subtype.
    text = str(exc)
    assert "error result: success" not in text, text
    assert result.result and result.result.strip() in text, (text, result.result)

    # Payload matches the ResultMessage that preceded it.
    assert exc.subtype == result.subtype
    assert exc.result == result.result
    assert exc.session_id == result.session_id
    assert exc.terminal_reason == result.terminal_reason
    assert exc.api_error_status == result.api_error_status
    assert exc.data.get("uuid") == result.uuid
    # This is the "mid-turn API failure" shape specifically.
    assert exc.subtype == "success"
    assert exc.terminal_reason == "api_error"
