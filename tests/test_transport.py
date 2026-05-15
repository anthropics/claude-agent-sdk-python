"""Tests for Claude SDK transport layer."""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions

DEFAULT_CLI_PATH = "/usr/bin/claude"
_ABSENT = object()  # sentinel for "field not sent on the wire"


def make_options(**kwargs: object) -> ClaudeAgentOptions:
    """Construct options using the standard CLI path unless overridden."""

    cli_path = kwargs.pop("cli_path", DEFAULT_CLI_PATH)
    return ClaudeAgentOptions(cli_path=cli_path, **kwargs)


class TestSubprocessCLITransport:
    """Test subprocess transport implementation."""

    def test_find_cli_not_found(self):
        """Test CLI not found error is raised during connect()."""

        async def _test():
            from claude_agent_sdk._errors import CLINotFoundError

            transport = SubprocessCLITransport(
                prompt="test", options=ClaudeAgentOptions()
            )
            assert transport._cli_path is None

            with (
                patch(
                    "claude_agent_sdk._internal.transport.subprocess_cli.shutil.which",
                    return_value=None,
                ),
                patch("pathlib.Path.exists", return_value=False),
                pytest.raises(CLINotFoundError) as exc_info,
            ):
                await transport.connect()

            assert "Claude Code not found" in str(exc_info.value)

        anyio.run(_test)

    def test_init_does_not_call_find_cli(self):
        """Test that __init__ defers CLI discovery instead of blocking."""
        transport = SubprocessCLITransport(prompt="test", options=ClaudeAgentOptions())
        assert transport._cli_path is None

    def test_init_uses_provided_cli_path(self):
        """Test that __init__ uses cli_path when provided."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=ClaudeAgentOptions(cli_path="/usr/bin/claude"),
        )
        assert transport._cli_path == "/usr/bin/claude"

    def test_build_command_basic(self):
        """Test building basic CLI command."""
        transport = SubprocessCLITransport(prompt="Hello", options=make_options())

        cmd = transport._build_command()
        assert cmd[0] == "/usr/bin/claude"
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        # Always use streaming mode (matching TypeScript SDK)
        assert "--input-format" in cmd
        assert "--print" not in cmd  # Never use --print anymore
        # Prompt is sent via stdin, not CLI args
        assert "--system-prompt" in cmd
        assert cmd[cmd.index("--system-prompt") + 1] == ""

    def test_build_command_include_hook_events(self):
        """Test that include_hook_events emits the --include-hook-events flag."""
        transport = SubprocessCLITransport(
            prompt="Hello", options=make_options(include_hook_events=True)
        )
        cmd = transport._build_command()
        assert "--include-hook-events" in cmd

        transport_off = SubprocessCLITransport(prompt="Hello", options=make_options())
        cmd_off = transport_off._build_command()
        assert "--include-hook-events" not in cmd_off

    def test_build_command_strict_mcp_config(self):
        """Test that --strict-mcp-config is emitted only when enabled."""
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(strict_mcp_config=True)
        )
        assert "--strict-mcp-config" in transport._build_command()

        transport = SubprocessCLITransport(prompt="test", options=make_options())
        assert "--strict-mcp-config" not in transport._build_command()

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

    def test_build_command_with_effort_xhigh(self):
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(effort="xhigh"),
        )

        cmd = transport._build_command()
        assert "--effort" in cmd
        assert cmd[cmd.index("--effort") + 1] == "xhigh"

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

    def test_build_command_with_system_prompt_file(self):
        """Test building CLI command with system prompt file."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={"type": "file", "path": "/path/to/prompt.md"},
            ),
        )

        cmd = transport._build_command()
        assert "--system-prompt" not in cmd
        assert "--append-system-prompt" not in cmd
        assert "--system-prompt-file" in cmd
        assert "/path/to/prompt.md" in cmd

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

    def test_build_command_with_dont_ask_permission_mode(self):
        """Test building CLI command with dontAsk permission mode."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(permission_mode="dontAsk"),
        )

        cmd = transport._build_command()
        assert "--permission-mode" in cmd
        assert "dontAsk" in cmd

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

    def test_build_command_with_task_budget(self):
        """Test building CLI command with task_budget option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(task_budget={"total": 100000}),
        )

        cmd = transport._build_command()
        assert "--task-budget" in cmd
        assert "100000" in cmd

    def test_build_command_without_task_budget(self):
        """Test that --task-budget is not included when task_budget is None."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert "--task-budget" not in cmd

    def test_build_command_with_max_thinking_tokens(self):
        """Test building CLI command with max_thinking_tokens option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(max_thinking_tokens=5000),
        )

        cmd = transport._build_command()
        assert "--max-thinking-tokens" in cmd
        assert "5000" in cmd

    @pytest.mark.parametrize(
        ("thinking", "expected", "absent"),
        [
            ({"type": "adaptive"}, ["--thinking", "adaptive"], "--max-thinking-tokens"),
            (
                {"type": "enabled", "budget_tokens": 5000},
                ["--max-thinking-tokens", "5000"],
                "--thinking",
            ),
            ({"type": "disabled"}, ["--thinking", "disabled"], "--max-thinking-tokens"),
        ],
    )
    def test_build_command_with_thinking(self, thinking, expected, absent):
        """Test building CLI command with thinking option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(thinking=thinking),
        )

        cmd = transport._build_command()
        idx = cmd.index(expected[0])
        assert cmd[idx : idx + 2] == expected
        assert absent not in cmd

    @pytest.mark.parametrize(
        ("thinking", "expected_display"),
        [
            (
                {"type": "adaptive", "display": "summarized"},
                ["--thinking-display", "summarized"],
            ),
            (
                {"type": "enabled", "budget_tokens": 20000, "display": "omitted"},
                ["--thinking-display", "omitted"],
            ),
        ],
    )
    def test_build_command_thinking_display_forwarded(self, thinking, expected_display):
        """`display` in thinking config is forwarded as --thinking-display."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(thinking=thinking),
        )

        cmd = transport._build_command()
        idx = cmd.index(expected_display[0])
        assert cmd[idx : idx + 2] == expected_display

    def test_build_command_thinking_without_display(self):
        """Omitting `display` leaves --thinking-display off the command."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(thinking={"type": "adaptive"}),
        )

        cmd = transport._build_command()
        assert "--thinking-display" not in cmd

    def test_build_command_thinking_display_with_enabled_budget(self):
        """enabled + display emits both --max-thinking-tokens and --thinking-display."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                thinking={
                    "type": "enabled",
                    "budget_tokens": 20000,
                    "display": "omitted",
                }
            ),
        )

        cmd = transport._build_command()
        budget_idx = cmd.index("--max-thinking-tokens")
        assert cmd[budget_idx : budget_idx + 2] == ["--max-thinking-tokens", "20000"]
        display_idx = cmd.index("--thinking-display")
        assert cmd[display_idx : display_idx + 2] == ["--thinking-display", "omitted"]

    def test_build_command_thinking_precedence_over_max_thinking_tokens(self):
        """thinking takes precedence over deprecated max_thinking_tokens."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                thinking={"type": "adaptive"}, max_thinking_tokens=9999
            ),
        )

        cmd = transport._build_command()
        idx = cmd.index("--thinking")
        assert cmd[idx : idx + 2] == ["--thinking", "adaptive"]
        assert "--max-thinking-tokens" not in cmd

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

    def test_session_id(self):
        """Test custom session ID option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(session_id="550e8400-e29b-41d4-a716-446655440000"),
        )

        cmd = transport._build_command()
        assert "--session-id" in cmd
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "550e8400-e29b-41d4-a716-446655440000"

    def test_session_id_not_set_by_default(self):
        """Test that --session-id is not passed when session_id is None."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert "--session-id" not in cmd

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
                # After stdin EOF, the process is given time to exit
                # gracefully. Since the mock's wait() returns immediately,
                # terminate should NOT be called.
                mock_process.terminate.assert_not_called()
                mock_process.wait.assert_called()

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

    def test_build_command_setting_sources_omitted_when_not_provided(self):
        """Test that --setting-sources is omitted when setting_sources is not provided."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )
        cmd = transport._build_command()
        assert not any(a.startswith("--setting-sources") for a in cmd)

    def test_build_command_setting_sources_empty_list_disables_all(self):
        """Test that setting_sources=[] passes --setting-sources= to disable all sources."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(setting_sources=[]),
        )
        cmd = transport._build_command()
        assert "--setting-sources=" in cmd

    def test_build_command_setting_sources_included_when_provided(self):
        """Test that --setting-sources is included when setting_sources has values."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(setting_sources=["user", "project"]),
        )
        cmd = transport._build_command()
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_none_leaves_options_untouched(self):
        """When skills is None (default), neither allowed_tools nor setting_sources change."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )
        cmd = transport._build_command()
        assert "--allowedTools" not in cmd
        assert not any(a.startswith("--setting-sources") for a in cmd)

    def test_build_command_skills_all_enables_skill_tool(self):
        """skills='all' enables the bare Skill tool and defaults setting_sources."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills="all"),
        )
        cmd = transport._build_command()
        assert "--allowedTools" in cmd
        assert cmd[cmd.index("--allowedTools") + 1] == "Skill"
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_empty_list_adds_no_skill_entries(self):
        """skills=[] is a degenerate subset: setting_sources defaults, no Skill entries."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[]),
        )
        cmd = transport._build_command()
        assert "--allowedTools" not in cmd
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_named_list_uses_skill_patterns(self):
        """Non-empty skills list adds Skill(name) entries and defaults setting_sources."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=["pdf", "docx"]),
        )
        cmd = transport._build_command()
        assert "--allowedTools" in cmd
        assert cmd[cmd.index("--allowedTools") + 1] == "Skill(pdf),Skill(docx)"
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_merges_with_existing_allowed_tools(self):
        """skills augment (not replace) an existing allowed_tools list."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                allowed_tools=["Read", "Write"],
                skills=["pdf"],
            ),
        )
        cmd = transport._build_command()
        assert cmd[cmd.index("--allowedTools") + 1] == "Read,Write,Skill(pdf)"

    def test_build_command_skills_preserves_user_setting_sources(self):
        """When setting_sources is explicitly provided, skills should not override it."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                skills="all",
                setting_sources=["local"],
            ),
        )
        cmd = transport._build_command()
        assert "--setting-sources=local" in cmd

    def test_build_command_skills_does_not_mutate_options(self):
        """Applying skills defaults must not mutate the caller's options object."""
        options = make_options(allowed_tools=["Read"], skills=["pdf"])
        transport = SubprocessCLITransport(prompt="test", options=options)
        transport._build_command()
        assert options.allowed_tools == ["Read"]
        assert options.setting_sources is None

    def test_build_command_skills_does_not_duplicate_entries(self):
        """Injecting Skill entries is idempotent when caller already listed them."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                allowed_tools=["Skill(pdf)"],
                skills=["pdf"],
            ),
        )
        cmd = transport._build_command()
        assert cmd[cmd.index("--allowedTools") + 1] == "Skill(pdf)"

    @pytest.mark.parametrize(
        ("skills", "extra", "want_tools", "want_sources", "want_init_skills"),
        [
            # (1) default: no auto-config
            (None, {}, None, None, _ABSENT),
            # (2) old manual way still works (skills=None, user wires it)
            (
                None,
                {
                    "allowed_tools": ["Skill", "Read"],
                    "setting_sources": ["user", "project"],
                },
                "Skill,Read",
                "user,project",
                _ABSENT,
            ),
            # (3) "all": bare Skill, default sources, no wire filter
            ("all", {}, "Skill", "user,project", _ABSENT),
            # (4) named subset
            (
                ["pdf", "docx"],
                {},
                "Skill(pdf),Skill(docx)",
                "user,project",
                ["pdf", "docx"],
            ),
            # (5) subset + explicit setting_sources (user wins)
            (
                ["pdf"],
                {"setting_sources": ["project"]},
                "Skill(pdf)",
                "project",
                ["pdf"],
            ),
            # (6) subset merges into existing allowed_tools
            (
                ["pdf"],
                {"allowed_tools": ["Read", "Bash"]},
                "Read,Bash,Skill(pdf)",
                "user,project",
                ["pdf"],
            ),
            # (7) empty list = degenerate subset (not "all")
            ([], {}, None, "user,project", []),
        ],
        ids=[
            "default-none",
            "old-manual",
            "all",
            "subset",
            "subset+explicit-sources",
            "subset+merge-tools",
            "empty-list",
        ],
    )
    def test_skills_option_matrix(
        self, skills, extra, want_tools, want_sources, want_init_skills
    ):
        """Documented behavior table for ClaudeAgentOptions.skills.

        Asserts the full (input) -> (allowedTools, setting_sources,
        initialize.skills) mapping in one place. See also
        test_query.py::test_initialize_* for the wire-level half.
        """
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=skills, **extra),
        )
        cmd = transport._build_command()

        if want_tools is None:
            assert "--allowedTools" not in cmd
        else:
            assert cmd[cmd.index("--allowedTools") + 1] == want_tools

        if want_sources is None:
            assert not any(a.startswith("--setting-sources") for a in cmd)
        else:
            assert f"--setting-sources={want_sources}" in cmd

        # Wire-level: what the Query layer would send on initialize.
        # 'all' and None both omit the field; only an explicit list is sent.
        if want_init_skills is _ABSENT:
            assert not isinstance(skills, list)
        else:
            assert skills == want_init_skills

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

                # Verify SDK entrypoint default is applied (overrides inherited env)
                assert "CLAUDE_CODE_ENTRYPOINT" in env_passed
                assert env_passed["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"

                # Verify system env vars are also included with correct values
                if "PATH" in os.environ:
                    assert "PATH" in env_passed
                    assert env_passed["PATH"] == os.environ["PATH"]

        anyio.run(_test)

    def test_caller_can_override_entrypoint(self):
        """Test that a caller-supplied CLAUDE_CODE_ENTRYPOINT survives the env merge."""

        async def _test():
            custom_env = {"CLAUDE_CODE_ENTRYPOINT": "custom-caller"}
            options = make_options(env=custom_env)

            with patch(
                "anyio.open_process", new_callable=AsyncMock
            ) as mock_open_process:
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # Caller's entrypoint must win over the sdk-py default
                assert env_passed["CLAUDE_CODE_ENTRYPOINT"] == "custom-caller"

                # CLAUDE_AGENT_SDK_VERSION is still SDK-controlled
                assert "CLAUDE_AGENT_SDK_VERSION" in env_passed

        anyio.run(_test)

    def test_otel_trace_context_propagated_to_subprocess(self):
        """Active OTEL trace context is injected as TRACEPARENT/TRACESTATE."""

        async def _test():
            options = make_options()

            def fake_inject(carrier: dict[str, str]) -> None:
                carrier["traceparent"] = (
                    "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
                )
                carrier["tracestate"] = "vendor=value"

            fake_propagate = MagicMock()
            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                assert (
                    env_passed["TRACEPARENT"]
                    == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
                )
                assert env_passed["TRACESTATE"] == "vendor=value"

        anyio.run(_test)

    def test_otel_trace_context_does_not_override_user_env(self):
        """User-supplied TRACEPARENT in options.env wins over OTEL propagator."""

        async def _test():
            options = make_options(env={"TRACEPARENT": "custom"})

            def fake_inject(carrier: dict[str, str]) -> None:
                carrier["traceparent"] = "00-aaaa-bbbb-01"

            fake_propagate = MagicMock()
            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # explicit ClaudeAgentOptions.env must win over the propagator
                assert env_passed["TRACEPARENT"] == "custom"

        anyio.run(_test)

    def test_otel_trace_context_noop_without_opentelemetry(self):
        """connect() succeeds and sets no TRACEPARENT when opentelemetry is absent."""

        async def _test():
            options = make_options()

            with (
                patch.dict("sys.modules", {"opentelemetry": None}),
                patch.dict(os.environ, {}, clear=False),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                # Ensure no inherited TRACEPARENT leaks in
                os.environ.pop("TRACEPARENT", None)

                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()  # must not raise

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]
                assert "TRACEPARENT" not in env_passed

        anyio.run(_test)

    def test_otel_trace_context_overwrites_inherited_env(self):
        """Active span's TRACEPARENT overwrites a stale value inherited from os.environ."""

        async def _test():
            options = make_options()

            stale = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
            active = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

            def fake_inject(carrier: dict[str, str]) -> None:
                # Propagator emits traceparent only (active span has empty
                # trace_state) — the common case.
                carrier["traceparent"] = active

            fake_propagate = MagicMock()
            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    os.environ,
                    {"TRACEPARENT": stale, "TRACESTATE": "vendor=stale"},
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # The stale ambient value from os.environ must be replaced by
                # the active span's context.
                assert env_passed["TRACEPARENT"] == active
                assert env_passed["TRACEPARENT"] != stale
                # And the stale inherited TRACESTATE must be scrubbed, not
                # paired with the fresh TRACEPARENT.
                assert "TRACESTATE" not in env_passed

        anyio.run(_test)

    def test_otel_no_active_span_preserves_inherited_env(self):
        """With opentelemetry installed but no active span, inherited W3C env passes through."""

        async def _test():
            options = make_options()

            inherited_tp = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"

            # inject() with no active span writes nothing to the carrier.
            fake_propagate = MagicMock()
            fake_propagate.inject = lambda carrier: None

            with (
                patch.dict(
                    os.environ,
                    {"TRACEPARENT": inherited_tp, "TRACESTATE": "vendor=abc"},
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # No active span -> we must NOT scrub the launcher's context.
                assert env_passed["TRACEPARENT"] == inherited_tp
                assert env_passed["TRACESTATE"] == "vendor=abc"

        anyio.run(_test)

    def test_otel_baggage_only_carrier_preserves_inherited_env(self):
        """Non-empty carrier without traceparent (e.g. baggage only) must not scrub inherited W3C env."""

        async def _test():
            options = make_options()

            inherited_tp = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"

            # Default global propagator is composite(tracecontext, baggage):
            # with baggage in Context but no active span, inject() emits a
            # baggage key only.
            def fake_inject(carrier: dict[str, str]) -> None:
                carrier["baggage"] = "user.id=123"

            fake_propagate = MagicMock()
            fake_propagate.inject = fake_inject

            with (
                patch.dict(
                    os.environ,
                    {"TRACEPARENT": inherited_tp, "TRACESTATE": "vendor=abc"},
                ),
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # Carrier was non-empty but had no traceparent -> still no
                # active span, so the launcher's W3C context must pass through.
                assert env_passed["TRACEPARENT"] == inherited_tp
                assert env_passed["TRACESTATE"] == "vendor=abc"

        anyio.run(_test)

    def test_otel_propagator_error_does_not_break_connect(self):
        """A raising propagator must not surface as CLIConnectionError."""

        async def _test():
            options = make_options()

            fake_propagate = MagicMock()
            fake_propagate.inject = MagicMock(side_effect=RuntimeError("boom"))

            with (
                patch.dict(
                    "sys.modules",
                    {
                        "opentelemetry": MagicMock(propagate=fake_propagate),
                        "opentelemetry.propagate": fake_propagate,
                    },
                ),
                patch.dict(os.environ, {}, clear=False),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                # Ensure no inherited TRACEPARENT leaks in
                os.environ.pop("TRACEPARENT", None)

                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()  # must not raise CLIConnectionError

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]
                assert "TRACEPARENT" not in env_passed

        anyio.run(_test)

    def test_claudecode_env_var_not_inherited(self):
        """Test that CLAUDECODE env var is filtered from the subprocess environment."""

        async def _test():
            options = make_options()

            with (
                patch.dict(os.environ, {"CLAUDECODE": "1"}),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [
                    mock_version_process,
                    mock_process,
                ]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # CLAUDECODE must NOT be inherited from the parent process
                assert "CLAUDECODE" not in env_passed

                # Other env vars should still be present
                assert "CLAUDE_CODE_ENTRYPOINT" in env_passed
                assert "CLAUDE_AGENT_SDK_VERSION" in env_passed

        anyio.run(_test)

    def test_claudecode_can_be_set_via_options_env(self):
        """Test that users can explicitly set CLAUDECODE via options.env."""

        async def _test():
            options = make_options(env={"CLAUDECODE": "1"})

            with (
                patch.dict(os.environ, {}, clear=False),
                patch(
                    "anyio.open_process", new_callable=AsyncMock
                ) as mock_open_process,
            ):
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.side_effect = [
                    mock_version_process,
                    mock_process,
                ]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args_list[1].kwargs["env"]

                # Explicit options.env should be respected
                assert env_passed.get("CLAUDECODE") == "1"

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

    def test_build_command_with_sandbox_only(self):
        """Test building CLI command with sandbox settings (no existing settings)."""
        import json

        from claude_agent_sdk import SandboxSettings

        sandbox: SandboxSettings = {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "network": {
                "allowLocalBinding": True,
                "allowUnixSockets": ["/var/run/docker.sock"],
            },
        }

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(sandbox=sandbox),
        )

        cmd = transport._build_command()

        # Should have --settings with sandbox merged in
        assert "--settings" in cmd
        settings_idx = cmd.index("--settings")
        settings_value = cmd[settings_idx + 1]

        # Parse and verify
        parsed = json.loads(settings_value)
        assert "sandbox" in parsed
        assert parsed["sandbox"]["enabled"] is True
        assert parsed["sandbox"]["autoAllowBashIfSandboxed"] is True
        assert parsed["sandbox"]["network"]["allowLocalBinding"] is True
        assert parsed["sandbox"]["network"]["allowUnixSockets"] == [
            "/var/run/docker.sock"
        ]

    def test_build_command_with_sandbox_and_settings_json(self):
        """Test building CLI command with sandbox merged into existing settings JSON."""
        import json

        from claude_agent_sdk import SandboxSettings

        # Existing settings as JSON string
        existing_settings = (
            '{"permissions": {"allow": ["Bash(ls:*)"]}, "verbose": true}'
        )

        sandbox: SandboxSettings = {
            "enabled": True,
            "excludedCommands": ["git", "docker"],
        }

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings=existing_settings, sandbox=sandbox),
        )

        cmd = transport._build_command()

        # Should have merged settings
        assert "--settings" in cmd
        settings_idx = cmd.index("--settings")
        settings_value = cmd[settings_idx + 1]

        parsed = json.loads(settings_value)

        # Original settings should be preserved
        assert parsed["permissions"] == {"allow": ["Bash(ls:*)"]}
        assert parsed["verbose"] is True

        # Sandbox should be merged in
        assert "sandbox" in parsed
        assert parsed["sandbox"]["enabled"] is True
        assert parsed["sandbox"]["excludedCommands"] == ["git", "docker"]

    def test_build_command_with_settings_file_and_no_sandbox(self):
        """Test that settings file path is passed through when no sandbox."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings="/path/to/settings.json"),
        )

        cmd = transport._build_command()

        # Should pass path directly, not parse it
        assert "--settings" in cmd
        settings_idx = cmd.index("--settings")
        assert cmd[settings_idx + 1] == "/path/to/settings.json"

    def test_build_command_sandbox_minimal(self):
        """Test sandbox with minimal configuration."""
        import json

        from claude_agent_sdk import SandboxSettings

        sandbox: SandboxSettings = {"enabled": True}

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(sandbox=sandbox),
        )

        cmd = transport._build_command()

        assert "--settings" in cmd
        settings_idx = cmd.index("--settings")
        settings_value = cmd[settings_idx + 1]

        parsed = json.loads(settings_value)
        assert parsed == {"sandbox": {"enabled": True}}

    def test_sandbox_network_config(self):
        """Test sandbox with full network configuration."""
        import json

        from claude_agent_sdk import SandboxSettings

        sandbox: SandboxSettings = {
            "enabled": True,
            "network": {
                "allowUnixSockets": ["/tmp/ssh-agent.sock"],
                "allowAllUnixSockets": False,
                "allowLocalBinding": True,
                "httpProxyPort": 8080,
                "socksProxyPort": 8081,
            },
        }

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(sandbox=sandbox),
        )

        cmd = transport._build_command()
        settings_idx = cmd.index("--settings")
        settings_value = cmd[settings_idx + 1]

        parsed = json.loads(settings_value)
        network = parsed["sandbox"]["network"]

        assert network["allowUnixSockets"] == ["/tmp/ssh-agent.sock"]
        assert network["allowAllUnixSockets"] is False
        assert network["allowLocalBinding"] is True
        assert network["httpProxyPort"] == 8080
        assert network["socksProxyPort"] == 8081

    def test_build_command_with_tools_array(self):
        """Test building CLI command with tools as array of tool names."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools=["Read", "Edit", "Bash"]),
        )

        cmd = transport._build_command()
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == "Read,Edit,Bash"

    def test_build_command_with_tools_empty_array(self):
        """Test building CLI command with tools as empty array (disables all tools)."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools=[]),
        )

        cmd = transport._build_command()
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == ""

    def test_build_command_with_tools_preset(self):
        """Test building CLI command with tools preset."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools={"type": "preset", "preset": "claude_code"}),
        )

        cmd = transport._build_command()
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == "default"

    def test_build_command_without_tools(self):
        """Test building CLI command without tools option (default None)."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert "--tools" not in cmd

    def test_concurrent_writes_are_serialized(self):
        """Test that concurrent write() calls are serialized by the lock.

        When parallel subagents invoke MCP tools, they trigger concurrent write()
        calls. Without the _write_lock, trio raises BusyResourceError.

        Uses a real subprocess with the same stream setup as production:
        process.stdin -> TextSendStream
        """

        async def _test():
            import sys
            from subprocess import PIPE

            from anyio.streams.text import TextSendStream

            # Create a real subprocess that consumes stdin (cross-platform)
            process = await anyio.open_process(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
            )

            try:
                transport = SubprocessCLITransport(
                    prompt="test",
                    options=ClaudeAgentOptions(cli_path="/usr/bin/claude"),
                )

                # Same setup as production: TextSendStream wrapping process.stdin
                transport._ready = True
                transport._process = MagicMock(returncode=None)
                transport._stdin_stream = TextSendStream(process.stdin)

                # Spawn concurrent writes - the lock should serialize them
                num_writes = 10
                errors: list[Exception] = []

                async def do_write(i: int):
                    try:
                        await transport.write(f'{{"msg": {i}}}\n')
                    except Exception as e:
                        errors.append(e)

                async with anyio.create_task_group() as tg:
                    for i in range(num_writes):
                        tg.start_soon(do_write, i)

                # All writes should succeed - the lock serializes them
                assert len(errors) == 0, f"Got errors: {errors}"
            finally:
                process.terminate()
                await process.wait()

        anyio.run(_test, backend="trio")

    def test_concurrent_writes_fail_without_lock(self):
        """Verify that without the lock, concurrent writes cause BusyResourceError.

        Uses a real subprocess with the same stream setup as production.
        """

        async def _test():
            import sys
            from contextlib import asynccontextmanager
            from subprocess import PIPE

            from anyio.streams.text import TextSendStream

            # Create a real subprocess that consumes stdin (cross-platform)
            process = await anyio.open_process(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
            )

            try:
                transport = SubprocessCLITransport(
                    prompt="test",
                    options=ClaudeAgentOptions(cli_path="/usr/bin/claude"),
                )

                # Same setup as production
                transport._ready = True
                transport._process = MagicMock(returncode=None)
                transport._stdin_stream = TextSendStream(process.stdin)

                # Replace lock with no-op to trigger the race condition
                class NoOpLock:
                    @asynccontextmanager
                    async def __call__(self):
                        yield

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        pass

                transport._write_lock = NoOpLock()

                # Spawn concurrent writes - should fail without lock
                num_writes = 10
                errors: list[Exception] = []

                async def do_write(i: int):
                    try:
                        await transport.write(f'{{"msg": {i}}}\n')
                    except Exception as e:
                        errors.append(e)

                async with anyio.create_task_group() as tg:
                    for i in range(num_writes):
                        tg.start_soon(do_write, i)

                # Should have gotten errors due to concurrent access
                assert len(errors) > 0, (
                    "Expected errors from concurrent access, but got none"
                )

                # Check that at least one error mentions the concurrent access
                error_strs = [str(e) for e in errors]
                assert any("another task" in s for s in error_strs), (
                    f"Expected 'another task' error, got: {error_strs}"
                )
            finally:
                process.terminate()
                await process.wait()

        anyio.run(_test, backend="trio")

    def test_close_terminates_after_grace_period_timeout(self):
        """Test that SIGTERM and then SIGKILL are sent when process never exits."""

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

                # Mock main process that hangs (never exits on its own)
                mock_process = MagicMock()
                mock_process.returncode = None
                mock_process.terminate = MagicMock()
                mock_process.kill = MagicMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()

                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin

                # Make wait() hang until cancelled (simulates stuck process)
                async def hanging_wait():
                    await anyio.sleep(999)

                mock_process.wait = AsyncMock(side_effect=hanging_wait)

                mock_exec.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=make_options(),
                )

                await transport.connect()

                with patch("anyio.fail_after", side_effect=TimeoutError):
                    mock_process.wait = AsyncMock()
                    await transport.close()

                mock_process.terminate.assert_called_once()
                mock_process.kill.assert_called_once()

        anyio.run(_test)

    def test_close_sigterm_succeeds_no_sigkill(self):
        """Test that SIGKILL is NOT sent when process exits after SIGTERM."""

        async def _test():
            with patch("anyio.open_process") as mock_exec:
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.returncode = None
                mock_process.terminate = MagicMock()
                mock_process.kill = MagicMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()

                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin

                mock_process.wait = AsyncMock()

                mock_exec.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=make_options(),
                )

                await transport.connect()

                # First fail_after raises (grace period expired), second
                # succeeds (process responds to SIGTERM)
                call_count = 0

                def mock_fail_after(timeout):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        raise TimeoutError
                    return nullcontext()

                with patch("anyio.fail_after", side_effect=mock_fail_after):
                    await transport.close()

                mock_process.terminate.assert_called_once()
                mock_process.kill.assert_not_called()

        anyio.run(_test)

    def test_close_skips_wait_when_already_exited(self):
        """Test that close() doesn't wait or terminate if process already exited."""

        async def _test():
            with patch("anyio.open_process") as mock_exec:
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"2.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_process = MagicMock()
                mock_process.returncode = 0  # Already exited
                mock_process.terminate = MagicMock()
                mock_process.wait = AsyncMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()

                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin

                mock_exec.side_effect = [mock_version_process, mock_process]

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=make_options(),
                )

                await transport.connect()
                await transport.close()

                # Should not try to wait or terminate an already-exited process
                mock_process.terminate.assert_not_called()

        anyio.run(_test)

    def test_build_command_agents_always_via_initialize(self):
        """Test that --agents is NEVER passed via CLI.

        Matching TypeScript SDK behavior, agents are always sent via the
        initialize request through stdin, regardless of prompt type.
        """
        from claude_agent_sdk.types import AgentDefinition

        agents = {
            "test-agent": AgentDefinition(
                description="A test agent",
                prompt="You are a test agent",
            )
        }

        # Test with string prompt
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(agents=agents),
        )
        cmd = transport._build_command()
        assert "--agents" not in cmd
        assert "--input-format" in cmd
        assert "stream-json" in cmd

        # Test with async iterable prompt
        async def fake_stream():
            yield {"type": "user", "message": {"role": "user", "content": "test"}}

        transport2 = SubprocessCLITransport(
            prompt=fake_stream(),
            options=make_options(agents=agents),
        )
        cmd2 = transport2._build_command()
        assert "--agents" not in cmd2
        assert "--input-format" in cmd2
        assert "stream-json" in cmd2

    def test_build_command_always_uses_streaming(self):
        """Test that streaming mode is always used, even for string prompts.

        Matching TypeScript SDK behavior, we always use --input-format stream-json
        so that agents and other large configs can be sent via initialize request.
        """
        # String prompt should still use streaming
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(),
        )
        cmd = transport._build_command()
        assert "--input-format" in cmd
        assert "stream-json" in cmd
        assert "--print" not in cmd

    def test_build_command_large_agents_work(self):
        """Test that large agent definitions work without size limits.

        Since agents are sent via initialize request through stdin,
        there are no ARG_MAX or command line length limits.
        """
        from claude_agent_sdk.types import AgentDefinition

        # Create a large agent definition (50KB prompt)
        large_prompt = "x" * 50000
        agents = {
            "large-agent": AgentDefinition(
                description="A large agent",
                prompt=large_prompt,
            )
        }

        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(agents=agents),
        )

        cmd = transport._build_command()

        # --agents should not be in command (sent via initialize)
        assert "--agents" not in cmd
        # No @filepath references should exist
        cmd_str = " ".join(cmd)
        assert "@" not in cmd_str

    def test_version_warning_includes_cli_path(self):
        """Test that the version warning includes the CLI path and uses only logger.warning."""

        async def _test():
            transport = SubprocessCLITransport(
                prompt="test",
                options=make_options(),
            )

            with (
                patch("anyio.open_process") as mock_exec,
                patch(
                    "claude_agent_sdk._internal.transport.subprocess_cli.logger"
                ) as mock_logger,
                patch("builtins.print") as mock_print,
            ):
                # Mock version check returning an old version
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"1.0.0 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_exec.return_value = mock_version_process

                await transport._check_claude_version()

                # logger.warning should be called with version and CLI path
                mock_logger.warning.assert_called_once()
                args, _ = mock_logger.warning.call_args
                assert args[1] == "1.0.0"
                assert args[2] == DEFAULT_CLI_PATH

                # print should NOT be called (no duplicate output)
                mock_print.assert_not_called()

        anyio.run(_test)

    def test_version_warning_not_emitted_for_current_version(self):
        """Test that no warning is emitted when CLI version meets minimum."""

        async def _test():
            transport = SubprocessCLITransport(
                prompt="test",
                options=make_options(),
            )

            with (
                patch("anyio.open_process") as mock_exec,
                patch(
                    "claude_agent_sdk._internal.transport.subprocess_cli.logger"
                ) as mock_logger,
            ):
                # Mock version check returning a current version
                mock_version_process = MagicMock()
                mock_version_process.stdout = MagicMock()
                mock_version_process.stdout.receive = AsyncMock(
                    return_value=b"99.99.99 (Claude Code)"
                )
                mock_version_process.terminate = MagicMock()
                mock_version_process.wait = AsyncMock()

                mock_exec.return_value = mock_version_process

                await transport._check_claude_version()

                # No warning for a current version
                mock_logger.warning.assert_not_called()

        anyio.run(_test)

    def test_stderr_callback_raise_does_not_terminate_loop(self) -> None:
        """Regression for issue #929: a raise from ``options.stderr`` must not
        kill the read loop. Previously the outer ``except Exception: pass``
        caught it, exited the ``async for``, and silently dropped every
        subsequent stderr line for the rest of the session."""

        async def _test() -> None:
            received: list[str] = []

            def stderr_cb(line: str) -> None:
                received.append(line)
                if len(received) == 1:
                    raise RuntimeError("simulated handler failure")

            transport = SubprocessCLITransport(
                prompt="x", options=ClaudeAgentOptions(stderr=stderr_cb)
            )

            async def mock_iter() -> AsyncIterator[str]:
                yield "line 1"
                yield "line 2"
                yield "line 3"

            transport._stderr_stream = mock_iter()  # type: ignore[assignment]
            await transport._handle_stderr()

            # All three lines must be delivered despite the first raise.
            assert received == ["line 1", "line 2", "line 3"]

        anyio.run(_test)


class TestAtexitChildCleanup:
    """Tests for the atexit handler that terminates orphaned CLI subprocesses."""

    def test_kill_active_children_terminates_process(self) -> None:
        import sys

        from claude_agent_sdk._internal.transport import subprocess_cli

        async def _test() -> None:
            proc = await anyio.open_process(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
            subprocess_cli._ACTIVE_CHILDREN.add(proc)
            try:
                assert proc.returncode is None

                subprocess_cli._kill_active_children()

                assert not subprocess_cli._ACTIVE_CHILDREN
                with anyio.fail_after(5):
                    await proc.wait()
                assert proc.returncode is not None
            finally:
                subprocess_cli._ACTIVE_CHILDREN.discard(proc)
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

        anyio.run(_test)
