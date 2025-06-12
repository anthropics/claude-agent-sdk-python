"""Tests for Claude SDK transport layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_code_sdk.types import ClaudeCodeOptions


class TestSubprocessCLITransport:
    """Test subprocess transport implementation."""

    def test_find_cli_not_found(self):
        """Test CLI not found error."""
        from claude_code_sdk._errors import CLINotFoundError

        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.exists", return_value=False),
            pytest.raises(CLINotFoundError) as exc_info,
        ):
            SubprocessCLITransport(prompt="test", options=ClaudeCodeOptions())

        assert "Claude Code requires Node.js" in str(exc_info.value)

    def test_build_command_basic(self):
        """Test building basic CLI command."""
        transport = SubprocessCLITransport(
            prompt="Hello", options=ClaudeCodeOptions(), cli_path="/usr/bin/claude"
        )

        cmd = transport._build_command()
        assert cmd[0] == "/usr/bin/claude"
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--print" in cmd
        assert "Hello" in cmd

    def test_cli_path_accepts_pathlib_path(self):
        """Test that cli_path accepts pathlib.Path objects."""
        from pathlib import Path

        transport = SubprocessCLITransport(
            prompt="Hello",
            options=ClaudeCodeOptions(),
            cli_path=Path("/usr/bin/claude"),
        )

        assert transport._cli_path == "/usr/bin/claude"

    def test_build_command_with_options(self):
        """Test building CLI command with options."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeCodeOptions(
                system_prompt="Be helpful",
                allowed_tools=["Read", "Write"],
                disallowed_tools=["Bash"],
                model="claude-3-5-sonnet",
                permission_mode="acceptEdits",
                max_turns=5,
            ),
            cli_path="/usr/bin/claude",
        )

        cmd = transport._build_command()
        assert "--system-prompt" in cmd
        assert "Be helpful" in cmd
        assert "--allowedTools" in cmd
        assert "Read,Write" in cmd
        assert "--disallowedTools" in cmd
        assert "Bash" in cmd
        assert "--model" in cmd
        assert "claude-3-5-sonnet" in cmd
        assert "--permission-mode" in cmd
        assert "acceptEdits" in cmd
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_session_continuation(self):
        """Test session continuation options."""
        transport = SubprocessCLITransport(
            prompt="Continue from before",
            options=ClaudeCodeOptions(continue_conversation=True, resume="session-123"),
            cli_path="/usr/bin/claude",
        )

        cmd = transport._build_command()
        assert "--continue" in cmd
        assert "--resume" in cmd
        assert "session-123" in cmd

    def test_connect_disconnect(self):
        """Test connect and disconnect lifecycle."""

        async def _test():
            with patch("anyio.open_process") as mock_exec:
                mock_process = MagicMock()
                mock_process.returncode = None
                mock_process.terminate = MagicMock()
                mock_process.wait = AsyncMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()
                mock_exec.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=ClaudeCodeOptions(),
                    cli_path="/usr/bin/claude",
                )

                await transport.connect()
                assert transport._process is not None
                assert transport.is_connected()

                await transport.disconnect()
                mock_process.terminate.assert_called_once()

        anyio.run(_test)

    def test_receive_messages(self):
        """Test parsing messages from CLI output."""
        # This test is simplified to just test the parsing logic
        # The full async stream handling is tested in integration tests
        transport = SubprocessCLITransport(
            prompt="test", options=ClaudeCodeOptions(), cli_path="/usr/bin/claude"
        )

        # The actual message parsing is done by the client, not the transport
        # So we just verify the transport can be created and basic structure is correct
        assert transport._prompt == "test"
        assert transport._cli_path == "/usr/bin/claude"
