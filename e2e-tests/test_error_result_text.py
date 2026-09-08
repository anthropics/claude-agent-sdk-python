"""End-to-end test for the error text attached to a failed query.

When the CLI ends a turn with ``is_error: true`` it exits non-zero on purpose,
and the SDK replaces the generic "exit code 1" ProcessError with the error the
CLI already reported in its result message. For an API error (here: a model
that does not exist) the CLI emits ``subtype: "success"`` with the error text
in ``result`` rather than ``errors``; that text must reach the exception.
"""

from pathlib import Path

import pytest

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query


@pytest.mark.e2e
@pytest.mark.anyio
async def test_api_error_result_text_reaches_exception(tmp_path: Path):
    options = ClaudeAgentOptions(
        model="claude-not-a-real-model-e2e",
        max_turns=1,
        cwd=str(tmp_path),
    )

    result: ResultMessage | None = None
    with pytest.raises(Exception) as exc_info:
        async for message in query(prompt="Say hello", options=options):
            if isinstance(message, ResultMessage):
                result = message

    assert result is not None, "expected a result message before the exit error"
    assert result.is_error is True
    assert result.subtype == "success"
    assert result.result, "expected the CLI to put the error text in result"

    text = str(exc_info.value)
    assert text.startswith("Claude Code returned an error result: ")
    # The exception must carry the CLI's actual error text, not the subtype.
    assert text != "Claude Code returned an error result: success"
    assert result.result in text
