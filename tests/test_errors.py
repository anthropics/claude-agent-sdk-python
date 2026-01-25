"""Tests for Claude SDK error handling."""

from claude_agent_sdk import (
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    SandboxFileWatcherError,
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

    def test_sandbox_file_watcher_error(self):
        """Test SandboxFileWatcherError."""
        error = SandboxFileWatcherError(
            path="/var/folders/abc/T/vscode-git-123.sock",
            error_code="EOPNOTSUPP"
        )
        assert isinstance(error, ClaudeSDKError)
        assert error.path == "/var/folders/abc/T/vscode-git-123.sock"
        assert error.error_code == "EOPNOTSUPP"
        assert "Sandbox file watcher failed" in str(error)
        assert "vscode-git-123.sock" in str(error)
        assert "EOPNOTSUPP" in str(error)
        assert "socket files" in str(error)  # Helpful message
