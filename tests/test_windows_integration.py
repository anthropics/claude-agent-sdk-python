"""Integration tests for Windows subprocess creation flags."""

import subprocess
import sys
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
class TestWindowsSubprocessCreation:
    """Tests that CREATE_NO_WINDOW is passed on Windows GUI apps."""

    async def test_connect_passes_creationflags_when_no_console(self, mock_claude_path):
        """Test that connect() passes CREATE_NO_WINDOW when parent has no console."""
        from claude_agent_sdk import ClaudeSDKClient
        from claude_agent_sdk.types import ClaudeAgentOptions

        # Mock no console attached and anyio.open_process to capture kwargs
        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli._should_suppress_console_window",
                return_value=True,
            ),
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.anyio.open_process"
            ) as mock_open_process,
        ):
            # Setup mock process
            mock_process = AsyncMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.stdin = None
            mock_open_process.return_value = mock_process

            # Create client and connect
            options = ClaudeAgentOptions(cli_path=mock_claude_path)
            client = ClaudeSDKClient(options)
            client._transport._cli_path = mock_claude_path

            with suppress(Exception):
                await client._transport.connect()

            # Verify creationflags was passed
            assert mock_open_process.called
            call_kwargs = mock_open_process.call_args[1]
            assert "creationflags" in call_kwargs
            assert call_kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW

    async def test_version_check_passes_creationflags_when_no_console(
        self, mock_claude_path
    ):
        """Test that version check passes CREATE_NO_WINDOW when parent has no console."""
        from claude_agent_sdk import ClaudeSDKClient
        from claude_agent_sdk.types import ClaudeAgentOptions

        # Mock no console attached and anyio.open_process to capture kwargs
        with (
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli._should_suppress_console_window",
                return_value=True,
            ),
            patch(
                "claude_agent_sdk._internal.transport.subprocess_cli.anyio.open_process"
            ) as mock_open_process,
        ):
            # Setup mock process with version output
            mock_process = AsyncMock()
            mock_stdout = AsyncMock()
            mock_stdout.receive = AsyncMock(return_value=b"2.1.61\n")
            mock_process.stdout = mock_stdout
            mock_process.terminate = MagicMock()
            mock_process.wait = AsyncMock()
            mock_open_process.return_value = mock_process

            # Create client and trigger version check
            options = ClaudeAgentOptions(cli_path=mock_claude_path)
            client = ClaudeSDKClient(options)
            client._transport._cli_path = mock_claude_path

            with suppress(Exception):
                await client._transport._check_claude_version()

            # Verify creationflags was passed
            assert mock_open_process.called
            call_kwargs = mock_open_process.call_args[1]
            assert "creationflags" in call_kwargs
            assert call_kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
