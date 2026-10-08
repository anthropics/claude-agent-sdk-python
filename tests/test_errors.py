"""Tests for Claude SDK error handling."""

from claude_agent_sdk import (
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultError,
)


class TestErrorTypes:
    """Test error types and their properties."""

    def test_base_error(self):
        """Test base ClaudeSDKError."""
        error = ClaudeSDKError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert isinstance(error, Exception)

    def test_cli_not_found_error(self):
        """Test CLINotFoundError."""
        error = CLINotFoundError("Claude Code not found")
        assert isinstance(error, ClaudeSDKError)
        assert "Claude Code not found" in str(error)

    def test_connection_error(self):
        """Test CLIConnectionError."""
        error = CLIConnectionError("Failed to connect to CLI")
        assert isinstance(error, ClaudeSDKError)
        assert "Failed to connect to CLI" in str(error)

    def test_process_error(self):
        """Test ProcessError with exit code and stderr."""
        error = ProcessError("Process failed", exit_code=1, stderr="Command not found")
        assert error.exit_code == 1
        assert error.stderr == "Command not found"
        assert "Process failed" in str(error)
        assert "exit code: 1" in str(error)
        assert "Command not found" in str(error)

    def test_result_error_carries_payload(self):
        """ResultError is a ProcessError that exposes the result payload."""
        data = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "errors": [],
            "result": "API Error: Stream idle timeout - no chunks received",
            "api_error_status": None,
            "terminal_reason": "api_error",
            "session_id": "s-1",
        }
        error = ResultError("Claude Code returned an error result: x", data, 1)
        assert isinstance(error, ProcessError)
        assert isinstance(error, ClaudeSDKError)
        assert error.exit_code == 1
        assert error.data is data
        assert error.subtype == "success"
        assert error.errors == []
        assert error.result == "API Error: Stream idle timeout - no chunks received"
        assert error.api_error_status is None
        assert error.terminal_reason == "api_error"
        assert error.session_id == "s-1"
        assert "exit code: 1" in str(error)

    def test_result_error_tolerates_missing_or_malformed_fields(self):
        error = ResultError("boom", {"errors": 42, "api_error_status": "500"})
        assert error.subtype is None
        assert error.errors == []
        assert error.result is None
        assert error.api_error_status is None
        assert error.terminal_reason is None
        assert error.session_id is None
        assert error.exit_code is None
        assert ResultError("boom").data == {}

    def test_result_error_normalizes_errors_like_the_message_text(self):
        """A bare-string `errors` is kept and blank entries are dropped, so the
        structured field agrees with the text the reader builds from it."""
        assert ResultError("m", {"errors": "boom"}).errors == ["boom"]
        assert ResultError("m", {"errors": [" ", "x ", 3]}).errors == ["x"]

    def test_result_error_survives_pickle_and_copy(self):
        """Exceptions cross process boundaries via pickle (multiprocessing,
        ProcessPoolExecutor); ResultError must round-trip with its payload."""
        import copy
        import pickle

        data = {"subtype": "error_max_turns", "errors": ["too many"], "session_id": "s"}
        error = ResultError("Claude Code returned an error result: too many", data, 1)
        for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error)):
            assert type(clone) is ResultError
            assert str(clone) == str(error)
            assert clone.exit_code == 1
            assert clone.subtype == "error_max_turns"
            assert clone.errors == ["too many"]
            assert clone.session_id == "s"
            assert clone.data == data

    def test_json_decode_error(self):
        """Test CLIJSONDecodeError."""
        import json

        try:
            json.loads("{invalid json}")
        except json.JSONDecodeError as e:
            error = CLIJSONDecodeError("{invalid json}", e)
            assert error.line == "{invalid json}"
            assert error.original_error == e
            assert "Failed to decode JSON" in str(error)
