"""Tests for Claude SDK transport layer."""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions

DEFAULT_CLI_PATH = "/usr/bin/claude"


def make_options(**kwargs: object) -> ClaudeAgentOptions:
    """Construct options using the standard CLI path unless overridden."""

    cli_path = kwargs.pop("cli_path", DEFAULT_CLI_PATH)
    return ClaudeAgentOptions(cli_path=cli_path, **kwargs)


class TestSubprocessCLITransport:
    """Test subprocess transport implementation."""

    def test_find_cli_not_found(self):
        """Test CLI not found error."""
        from claude_agent_sdk._errors import CLINotFoundError

        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.exists", return_value=False),
            pytest.raises(CLINotFoundError) as exc_info,
        ):
            SubprocessCLITransport(prompt="test", options=ClaudeAgentOptions())

        assert "Claude Code not found" in str(exc_info.value)

    def test_build_command_basic(self):
        """Test building basic CLI command."""
        transport = SubprocessCLITransport(prompt="Hello", options=make_options())

        cmd = transport._build_command()
        assert cmd[0] == "/usr/bin/claude"
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--print" in cmd
        assert "Hello" in cmd
        assert "--system-prompt" in cmd
        assert cmd[cmd.index("--system-prompt") + 1] == ""

    def test_cli_path_accepts_pathlib_path(self):
        """Test that cli_path accepts pathlib.Path objects."""
        from pathlib import Path

        path = Path("/usr/bin/claude")
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=ClaudeAgentOptions(cli_path=path),
        )

        # Path object is converted to string, compare with str(path)
        assert transport._cli_path == str(path)

    def test_build_command_with_system_prompt_string(self):
        """Test building CLI command with system prompt as string."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt="Be helpful",
            ),
        )

        cmd = transport._build_command()
        assert "--system-prompt" in cmd
        assert "Be helpful" in cmd

    def test_build_command_with_system_prompt_preset(self):
        """Test building CLI command with system prompt preset."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={"type": "preset", "preset": "claude_code"},
            ),
        )

        cmd = transport._build_command()
        assert "--system-prompt" not in cmd
        assert "--append-system-prompt" not in cmd

    def test_build_command_with_system_prompt_preset_and_append(self):
        """Test building CLI command with system prompt preset and append."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": "Be concise.",
                },
            ),
        )

        cmd = transport._build_command()
        assert "--system-prompt" not in cmd
        assert "--append-system-prompt" in cmd
        assert "Be concise." in cmd

    def test_build_command_with_options(self):
        """Test building CLI command with options."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                allowed_tools=["Read", "Write"],
                disallowed_tools=["Bash"],
                model="claude-sonnet-4-5",
                permission_mode="acceptEdits",
                max_turns=5,
            ),
        )

        cmd = transport._build_command()
        assert "--allowedTools" in cmd
        assert "Read,Write" in cmd
        assert "--disallowedTools" in cmd
        assert "Bash" in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-5" in cmd
        assert "--permission-mode" in cmd
        assert "acceptEdits" in cmd
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_build_command_with_fallback_model(self):
        """Test building CLI command with fallback_model option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                model="opus",
                fallback_model="sonnet",
            ),
        )

        cmd = transport._build_command()
        assert "--model" in cmd
        assert "opus" in cmd
        assert "--fallback-model" in cmd
        assert "sonnet" in cmd

    def test_build_command_with_max_thinking_tokens(self):
        """Test building CLI command with max_thinking_tokens option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(max_thinking_tokens=5000),
        )

        cmd = transport._build_command()
        assert "--max-thinking-tokens" in cmd
        assert "5000" in cmd

    def test_build_command_with_add_dirs(self):
        """Test building CLI command with add_dirs option."""
        from pathlib import Path

        dir1 = "/path/to/dir1"
        dir2 = Path("/path/to/dir2")
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(add_dirs=[dir1, dir2]),
        )

        cmd = transport._build_command()

        # Check that both directories are in the command
        assert "--add-dir" in cmd
        add_dir_indices = [i for i, x in enumerate(cmd) if x == "--add-dir"]
        assert len(add_dir_indices) == 2

        # The directories should appear after --add-dir flags
        dirs_in_cmd = [cmd[i + 1] for i in add_dir_indices]
        assert dir1 in dirs_in_cmd
        assert str(dir2) in dirs_in_cmd

    def test_session_continuation(self):
        """Test session continuation options."""
        transport = SubprocessCLITransport(
            prompt="Continue from before",
            options=make_options(continue_conversation=True, resume="session-123"),
        )

        cmd = transport._build_command()
        assert "--continue" in cmd
        assert "--resume" in cmd
        assert "session-123" in cmd

    def test_connect_close(self):
        """Test connect and close lifecycle."""

        async def _test():
            with patch("anyio.open_process") as mock_exec:
                # Mock version check process
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                # Mock main process
                mock_process = MagicMock()
                mock_process.returncode = None
                mock_process.terminate = MagicMock()
                mock_process.wait = AsyncMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()

                # Mock stdin with aclose method
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin

                # Return version process first, then main process
                mock_exec.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=make_options(),
                )

                await transport.connect()
                assert transport._process is not None
                assert transport.is_ready()

                await transport.close()
                mock_process.terminate.assert_called_once()

        anyio.run(_test)

    def test_read_messages(self):
        """Test reading messages from CLI output."""
        # This test is simplified to just test the transport creation
        # The full async stream handling is tested in integration tests
        transport = SubprocessCLITransport(prompt="test", options=make_options())

        # The transport now just provides raw message reading via read_messages()
        # So we just verify the transport can be created and basic structure is correct
        assert transport._prompt == "test"
        assert transport._cli_path == "/usr/bin/claude"

    def test_connect_with_nonexistent_cwd(self):
        """Test that connect raises CLIConnectionError when cwd doesn't exist."""
        from claude_agent_sdk._errors import CLIConnectionError

        async def _test():
            transport = SubprocessCLITransport(
                prompt="test",
                options=make_options(cwd="/this/directory/does/not/exist"),
            )

            with pytest.raises(CLIConnectionError) as exc_info:
                await transport.connect()

            assert "/this/directory/does/not/exist" in str(exc_info.value)

        anyio.run(_test)

    def test_build_command_with_settings_file(self):
        """Test building CLI command with settings as file path."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings="/path/to/settings.json"),
        )

        cmd = transport._build_command()
        assert "--settings" in cmd
        assert "/path/to/settings.json" in cmd

    def test_build_command_with_settings_json(self):
        """Test building CLI command with settings as JSON object."""
        settings_json = '{"permissions": {"allow": ["Bash(ls:*)"]}}'
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings=settings_json),
        )

        cmd = transport._build_command()
        assert "--settings" in cmd
        assert settings_json in cmd

    def test_build_command_with_extra_args(self):
        """Test building CLI command with extra_args for future flags."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                extra_args={
                    "new-flag": "value",
                    "boolean-flag": None,
                    "another-option": "test-value",
                }
            ),
        )

        cmd = transport._build_command()
        cmd_str = " ".join(cmd)

        # Check flags with values
        assert "--new-flag value" in cmd_str
        assert "--another-option test-value" in cmd_str

        # Check boolean flag (no value)
        assert "--boolean-flag" in cmd
        # Make sure boolean flag doesn't have a value after it
        boolean_idx = cmd.index("--boolean-flag")
        # Either it's the last element or the next element is another flag
        assert boolean_idx == len(cmd) - 1 or cmd[boolean_idx + 1].startswith("--")

    def test_build_command_with_mcp_servers(self):
        """Test building CLI command with mcp_servers option."""
        import json

        mcp_servers = {
            "test-server": {
                "type": "stdio",
                "command": "/path/to/server",
                "args": ["--option", "value"],
            }
        }

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=mcp_servers),
        )

        cmd = transport._build_command()

        # Find the --mcp-config flag and its value
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        mcp_config_value = cmd[mcp_idx + 1]

        # Parse the JSON and verify structure
        config = json.loads(mcp_config_value)
        assert "mcpServers" in config
        assert config["mcpServers"] == mcp_servers

    def test_build_command_with_mcp_servers_as_file_path(self):
        """Test building CLI command with mcp_servers as file path."""
        from pathlib import Path

        # Test with string path
        string_path = "/path/to/mcp-config.json"
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=string_path),
        )

        cmd = transport._build_command()
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == string_path

        # Test with Path object
        path_obj = Path("/path/to/mcp-config.json")
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=path_obj),
        )

        cmd = transport._build_command()
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        # Path object gets converted to string, compare with str(path_obj)
        assert cmd[mcp_idx + 1] == str(path_obj)

    def test_build_command_with_mcp_servers_as_json_string(self):
        """Test building CLI command with mcp_servers as JSON string."""
        json_config = '{"mcpServers": {"server": {"type": "stdio", "command": "test"}}}'
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=json_config),
        )

        cmd = transport._build_command()
        assert "--mcp-config" in cmd
        mcp_idx = cmd.index("--mcp-config")
        assert cmd[mcp_idx + 1] == json_config

    def test_env_vars_passed_to_subprocess(self):
        """Test that custom environment variables are passed to the subprocess."""

        async def _test():
            test_value = f"test-{uuid.uuid4().hex[:8]}"
            custom_env = {
                "MY_TEST_VAR": test_value,
            }

            options = make_options(env=custom_env)

            # Mock the subprocess to capture the env argument
            with patch(
                "anyio.open_process", new_callable=AsyncMock
            ) as mock_open_process:
                # Mock version check process
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                # Mock main process
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()  # Add async aclose method
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                # Return version process first, then main process
                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )

                await transport.connect()

                # Verify open_process was called twice (version check + main process)
                assert mock_open_process.call_count == 2

                # Check the second call (main process) for env vars
                second_call_kwargs = mock_open_process.call_args_list[1].kwargs
                assert "env" in second_call_kwargs
                env_passed = second_call_kwargs["env"]

                # Check that custom env var was passed
                assert env_passed["MY_TEST_VAR"] == test_value

                # Verify SDK identifier is present
                assert "CLAUDE_CODE_ENTRYPOINT" in env_passed
                assert env_passed["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"

                # Verify system env vars are also included with correct values
                if "PATH" in os.environ:
                    assert "PATH" in env_passed
                    assert env_passed["PATH"] == os.environ["PATH"]

        anyio.run(_test)

    def test_connect_as_different_user(self):
        """Test connect as different user."""

        async def _test():
            custom_user = "claude"
            options = make_options(user=custom_user)

            # Mock the subprocess to capture the env argument
            with patch(
                "anyio.open_process", new_callable=AsyncMock
            ) as mock_open_process:
                # Mock version check process
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                # Mock main process
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()  # Add async aclose method
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                # Return version process first, then main process
                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )

                await transport.connect()

                # Verify open_process was called twice (version check + main process)
                assert mock_open_process.call_count == 2

                # Check the second call (main process) for user
                second_call_kwargs = mock_open_process.call_args_list[1].kwargs
                assert "user" in second_call_kwargs
                user_passed = second_call_kwargs["user"]

                # Check that user was passed
                assert user_passed == "claude"

        anyio.run(_test)
