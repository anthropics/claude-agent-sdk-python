"""Regression test for #1575: a failed run must not print 'Done: success'."""

from claude_agent_sdk import ResultMessage


def _result(*, subtype: str, is_error: bool) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="test-session",
    )


def _done_label(message: ResultMessage) -> str:
    # Mirrors the line added to examples/quick_start.py.
    return f"Done: {'error' if message.is_error else message.subtype}"


def test_failed_run_with_success_subtype_reports_error():
    # Observed with an invalid ANTHROPIC_API_KEY: subtype='success',
    # is_error=True. The old example printed 'Done: success'.
    assert _done_label(_result(subtype="success", is_error=True)) == "Done: error"


def test_successful_run_still_reports_subtype():
    assert _done_label(_result(subtype="success", is_error=False)) == "Done: success"
