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


class SandboxFileWatcherError(ClaudeSDKError):
    """Raised when the CLI's sandbox file watcher fails.

    This typically happens on macOS when the sandbox tries to watch
    system temp directories containing socket files or other unwatchable
    file types. The CLI's file watcher throws EOPNOTSUPP or EINTR errors
    on these files.
    """

    def __init__(self, path: str, error_code: str):
        self.path = path
        self.error_code = error_code
        message = (
            f"Sandbox file watcher failed on '{path}' ({error_code}). "
            "This is a known issue with the Claude CLI's sandbox on macOS. "
            "The sandbox tries to watch system temp directories that contain "
            "socket files (from VSCode, Docker, etc.) which cannot be watched. "
            "Workarounds: 1) Disable sandbox (sandbox=None), "
            "2) Run in a container with a clean /tmp directory."
        )
        super().__init__(message)
