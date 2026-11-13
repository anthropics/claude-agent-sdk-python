"""Tests for Claude SDK transport layer."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from claude_agent_sdk._errors import CLIConnectionError, CLINotFoundError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions

DEFAULT_CLI_PATH = "/usr/bin/claude"
_ABSENT = object()  # sentinel for "field not sent on the wire"


def make_options(**kwargs: object) -> ClaudeAgentOptions:
    """Construct options using the standard CLI path unless overridden."""

    cli_path = kwargs.pop("cli_path", DEFAULT_CLI_PATH)
    return ClaudeAgentOptions(cli_path=cli_path, **kwargs)


def flag_values(cmd: list[str], flag: str) -> list[str]:
    """Values passed for flag, each expected as one "--flag=value" argument.

    Fails if the flag appears on its own, which would mean its value was
    passed as a separate argument.
    """
    assert flag not in cmd, f"{flag} was passed without its value attached"
    prefix = f"{flag}="
    return [arg[len(prefix) :] for arg in cmd if arg.startswith(prefix)]


def flag_value(cmd: list[str], flag: str) -> str:
    """The single value passed for flag as "--flag=value"."""
    values = flag_values(cmd, flag)
    assert len(values) == 1, f"expected one {flag}=..., got {values!r}"
    return values[0]


def two_argument_value(cmd: list[str], flag: str) -> str:
    """The single value passed for flag as two arguments, "--flag" then the value."""
    assert not any(arg.startswith(f"{flag}=") for arg in cmd), (
        f"{flag} was passed as {flag}=..., expected two arguments"
    )
    assert cmd.count(flag) == 1, f"expected {flag} once, got {cmd.count(flag)}"
    return cmd[cmd.index(flag) + 1]


class TestSubprocessCLITransport:
    """Test subprocess transport implementation."""

    def test_find_cli_not_found(self):
        """Test CLI not found error is raised during connect()."""

        async def _test():
            transport = SubprocessCLITransport(
                prompt="test", options=ClaudeAgentOptions()
            )
            assert transport._cli_path is None

            with (
                patch.dict(os.environ, {"PATH": ""}),
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
        assert flag_value(cmd, "--system-prompt") == ""

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

    def test_build_command_resume_and_session_id(self):
        """Test that resume and session_id are passed as --flag=value."""
        session_id = "8f8b1c0e-2b1e-4a3f-9c2d-5e6f7a8b9c0d"
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(resume="abc123", session_id=session_id),
        )
        cmd = transport._build_command()

        assert "--resume=abc123" in cmd
        assert f"--session-id={session_id}" in cmd
        # Never emitted as two separate argv tokens.
        assert "--resume" not in cmd
        assert "--session-id" not in cmd
        assert "abc123" not in cmd
        assert session_id not in cmd

    def test_build_command_resume_session_at_and_drops_turn(self):
        """Truncating-resume options are passed as --flag=value."""
        at = "0d78eb23-2d48-4741-b970-4ed0a3356cce"
        drops = "ce0a8011-2c8d-40f2-86e5-d6e1b0c041c0"
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(
                resume="abc123",
                fork_session=True,
                resume_session_at=at,
                resume_drops_turn=drops,
            ),
        )
        cmd = transport._build_command()

        assert f"--resume-session-at={at}" in cmd
        assert f"--resume-drops-turn={drops}" in cmd
        assert "--resume-session-at" not in cmd
        assert "--resume-drops-turn" not in cmd
        assert at not in cmd
        assert drops not in cmd

    def test_build_command_resume_drops_turn_omitted_by_default(self):
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(resume="abc123", resume_session_at="x"),
        )
        cmd = transport._build_command()
        assert "--resume-session-at=x" in cmd
        assert not any(a.startswith("--resume-drops-turn") for a in cmd)

    def test_build_command_empty_resume_drops_turn_is_forwarded(self):
        """An empty declaration must reach the CLI (which rejects it) rather
        than being dropped here and silently disarming the guard."""
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(
                resume="abc123", resume_session_at="x", resume_drops_turn=""
            ),
        )
        cmd = transport._build_command()
        assert "--resume-drops-turn=" in cmd

    def test_build_command_resume_and_session_id_do_not_inject_flags(self):
        """Dash-leading values must not become standalone argv flags.

        The CLI declares --resume with an optional value, so in the two-token
        form (["--resume", value]) a dash-leading value is parsed as a separate
        flag rather than as the option's value. Applications that route
        untrusted input into these options would then let an attacker inject
        arbitrary CLI flags. The --flag=value form binds the value to the flag,
        and the CLI rejects it as an invalid session ID.
        """
        transport = SubprocessCLITransport(
            prompt="Hello",
            options=make_options(resume="--evil", session_id="-r"),
        )
        cmd = transport._build_command()

        assert "--resume=--evil" in cmd
        assert "--session-id=-r" in cmd
        # The injected values never appear as standalone argv tokens...
        assert "--evil" not in cmd
        assert "-r" not in cmd
        # ...nor do the bare flags that would let the next token detach.
        assert "--resume" not in cmd
        assert "--session-id" not in cmd

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
        assert flag_value(cmd, "--effort") == "xhigh"

    def test_build_command_with_system_prompt_string(self):
        """Test building CLI command with system prompt as string."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt="Be helpful",
            ),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--system-prompt") == "Be helpful"

    def test_build_command_with_system_prompt_preset(self):
        """Test building CLI command with system prompt preset."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={"type": "preset", "preset": "claude_code"},
            ),
        )

        cmd = transport._build_command()
        assert flag_values(cmd, "--system-prompt") == []
        assert flag_values(cmd, "--append-system-prompt") == []

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
        assert flag_values(cmd, "--system-prompt") == []
        assert flag_value(cmd, "--append-system-prompt") == "Be concise."

    def test_build_command_with_system_prompt_file(self):
        """Test building CLI command with system prompt file."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={"type": "file", "path": "/path/to/prompt.md"},
            ),
        )

        cmd = transport._build_command()
        assert flag_values(cmd, "--system-prompt") == []
        assert flag_values(cmd, "--append-system-prompt") == []
        assert flag_value(cmd, "--system-prompt-file") == "/path/to/prompt.md"

    @pytest.mark.parametrize("prompt", ["Be helpful", "", "--help"])
    def test_build_command_with_system_prompt_custom(self, prompt):
        """The custom form reaches the CLI the same way a plain string does."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                system_prompt={"type": "custom", "prompt": prompt, "snapshot": False},
            ),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--system-prompt") == prompt
        assert flag_values(cmd, "--append-system-prompt") == []
        assert "--system-prompt-snapshot" not in cmd

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
        assert flag_value(cmd, "--allowedTools") == "Read,Write"
        assert flag_value(cmd, "--disallowedTools") == "Bash"
        assert flag_value(cmd, "--model") == "claude-sonnet-4-5"
        assert flag_value(cmd, "--permission-mode") == "acceptEdits"
        assert flag_value(cmd, "--max-turns") == "5"

    def test_build_command_with_dont_ask_permission_mode(self):
        """Test building CLI command with dontAsk permission mode."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(permission_mode="dontAsk"),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--permission-mode") == "dontAsk"

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
        assert flag_value(cmd, "--model") == "opus"
        assert flag_value(cmd, "--fallback-model") == "sonnet"

    def test_build_command_with_task_budget(self):
        """Test building CLI command with task_budget option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(task_budget={"total": 100000}),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--task-budget") == "100000"

    def test_build_command_without_task_budget(self):
        """Test that --task-budget is not included when task_budget is None."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert flag_values(cmd, "--task-budget") == []

    def test_build_command_with_max_thinking_tokens(self):
        """Test building CLI command with max_thinking_tokens option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(max_thinking_tokens=5000),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--max-thinking-tokens") == "5000"

    @pytest.mark.parametrize(
        ("thinking", "expected", "absent"),
        [
            ({"type": "adaptive"}, ("--thinking", "adaptive"), "--max-thinking-tokens"),
            (
                {"type": "enabled", "budget_tokens": 5000},
                ("--max-thinking-tokens", "5000"),
                "--thinking",
            ),
            ({"type": "disabled"}, ("--thinking", "disabled"), "--max-thinking-tokens"),
        ],
    )
    def test_build_command_with_thinking(self, thinking, expected, absent):
        """Test building CLI command with thinking option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(thinking=thinking),
        )

        cmd = transport._build_command()
        flag, value = expected
        assert flag_value(cmd, flag) == value
        assert flag_values(cmd, absent) == []

    @pytest.mark.parametrize(
        ("thinking", "expected_display"),
        [
            (
                {"type": "adaptive", "display": "summarized"},
                "summarized",
            ),
            (
                {"type": "enabled", "budget_tokens": 20000, "display": "omitted"},
                "omitted",
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
        assert flag_value(cmd, "--thinking-display") == expected_display

    def test_build_command_thinking_without_display(self):
        """Omitting `display` leaves --thinking-display off the command."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(thinking={"type": "adaptive"}),
        )

        cmd = transport._build_command()
        assert flag_values(cmd, "--thinking-display") == []

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
        assert flag_value(cmd, "--max-thinking-tokens") == "20000"
        assert flag_value(cmd, "--thinking-display") == "omitted"

    def test_build_command_thinking_precedence_over_max_thinking_tokens(self):
        """thinking takes precedence over deprecated max_thinking_tokens."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                thinking={"type": "adaptive"}, max_thinking_tokens=9999
            ),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--thinking") == "adaptive"
        assert flag_values(cmd, "--max-thinking-tokens") == []

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

        # One --add-dir=<dir> argument per directory, in order
        assert flag_values(cmd, "--add-dir") == [dir1, str(dir2)]

    def test_session_continuation(self):
        """Test session continuation options."""
        transport = SubprocessCLITransport(
            prompt="Continue from before",
            options=make_options(continue_conversation=True, resume="session-123"),
        )

        cmd = transport._build_command()
        assert "--continue" in cmd
        assert "--resume=session-123" in cmd

    def test_session_id(self):
        """Test custom session ID option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(session_id="550e8400-e29b-41d4-a716-446655440000"),
        )

        cmd = transport._build_command()
        assert "--session-id=550e8400-e29b-41d4-a716-446655440000" in cmd

    def test_session_id_not_set_by_default(self):
        """Test that --session-id is not passed when session_id is None."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert not any(arg.startswith("--session-id") for arg in cmd)

    def test_connect_close(self):
        """Test connect and close lifecycle."""

        async def _test():
            with patch("anyio.open_process") as mock_exec:
                # Mock the CLI process
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

                mock_exec.return_value = mock_process

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
        assert two_argument_value(cmd, "--settings") == "/path/to/settings.json"

    def test_build_command_with_settings_json(self):
        """Test building CLI command with settings as JSON object."""
        settings_json = '{"permissions": {"allow": ["Bash(ls:*)"]}}'
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings=settings_json),
        )

        cmd = transport._build_command()
        assert two_argument_value(cmd, "--settings") == settings_json

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
        assert flag_values(cmd, "--allowedTools") == []
        assert not any(a.startswith("--setting-sources") for a in cmd)

    def test_build_command_skills_all_enables_skill_tool(self):
        """skills='all' enables the bare Skill tool and defaults setting_sources."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills="all"),
        )
        cmd = transport._build_command()
        assert flag_value(cmd, "--allowedTools") == "Skill"
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_empty_list_adds_no_skill_entries(self):
        """skills=[] is a degenerate subset: setting_sources defaults, no Skill entries."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[]),
        )
        cmd = transport._build_command()
        assert flag_values(cmd, "--allowedTools") == []
        assert "--setting-sources=user,project" in cmd

    def test_build_command_skills_named_list_uses_skill_patterns(self):
        """Non-empty skills list adds Skill(name) entries and defaults setting_sources."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=["pdf", "docx"]),
        )
        cmd = transport._build_command()
        assert flag_value(cmd, "--allowedTools") == "Skill(pdf),Skill(docx)"
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
        assert flag_value(cmd, "--allowedTools") == "Read,Write,Skill(pdf)"

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
        assert flag_value(cmd, "--allowedTools") == "Skill(pdf)"

    @pytest.mark.parametrize(
        "hostile_name",
        [
            # Names containing rule-syntax delimiters cannot be represented
            # as a single Skill(name) entry and must be rejected, never
            # formatted into the --allowedTools value.
            "x),Bash(*",
            "safe),Bash,Skill(dummy",
            "name,with,commas",
            "unbalanced(",
            "unbalanced)",
            "()",
        ],
    )
    def test_build_command_skills_rejects_rule_syntax_delimiters(
        self, hostile_name: str
    ):
        """Skill names containing rule-syntax delimiters raise ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="Invalid skill name"):
            transport._build_command()

    @pytest.mark.parametrize(
        "hostile_name",
        ["with\nnewline", "with\ttab", "nul\x00byte", "del\x7fchar"],
    )
    def test_build_command_skills_rejects_control_characters(self, hostile_name: str):
        """Skill names containing control characters raise ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="Invalid skill name"):
            transport._build_command()

    @pytest.mark.parametrize("empty_name", ["", " ", "  \t "])
    def test_build_command_skills_rejects_empty_names(self, empty_name: str):
        """Empty or whitespace-only skill names raise ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[empty_name]),
        )
        with pytest.raises(ValueError, match="non-empty"):
            transport._build_command()

    def test_build_command_skills_rejects_non_string_names(self):
        """Non-string entries in the skills list raise TypeError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[42]),  # type: ignore[list-item]
        )
        with pytest.raises(TypeError, match="must be strings"):
            transport._build_command()

    @pytest.mark.parametrize(
        "wildcard_name",
        ["pdf:*", "my skill *", ":*"],
    )
    def test_build_command_skills_rejects_wildcard_suffix_names(
        self, wildcard_name: str
    ):
        """Wildcard-suffix skill names raise ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[wildcard_name]),
        )
        with pytest.raises(ValueError, match="wildcard-suffix"):
            transport._build_command()

    @pytest.mark.parametrize("skills", ["pdf", "pdf-tools", "ALL"])
    def test_build_command_skills_rejects_a_bare_string(self, skills: str):
        """A string is iterable, so skills="pdf" would build Skill(p),Skill(d),..."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=skills),  # type: ignore[arg-type]
        )
        with pytest.raises(TypeError, match="must be a list of skill names"):
            transport._build_command()

    @pytest.mark.parametrize(
        "skills",
        [("pdf",), {"pdf"}, (n for n in ["pdf"])],
        ids=["tuple", "set", "generator"],
    )
    def test_build_command_skills_rejects_non_list_iterables(self, skills):
        """These build Skill(name) rules but are dropped from initialize, so the
        session skill filter is never installed."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=skills),  # type: ignore[arg-type]
        )
        with pytest.raises(TypeError, match="must be a list of skill names"):
            transport._build_command()

    def test_build_command_skills_rejects_bare_wildcard(self):
        """A literal '*' name raises, pointing to the skills="all" option."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=["*"]),
        )
        with pytest.raises(ValueError, match='use skills="all"'):
            transport._build_command()

    def test_build_command_skills_rejects_unpaired_trailing_backslash(self):
        """A name ending in a single trailing backslash raises ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=["name\\"]),
        )
        with pytest.raises(ValueError, match="unpaired backslash"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", ["name\\\\", "name\\\\\\", "mid\\\\dle"])
    def test_build_command_skills_rejects_consecutive_backslashes(
        self, hostile_name: str
    ):
        """Consecutive backslashes collapse at parse time, renaming the skill."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="consecutive backslashes"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", ["\ufeffpdf", "pdf\ufeff"])
    def test_build_command_skills_rejects_byte_order_marks(self, hostile_name: str):
        """The CLI trims U+FEFF as whitespace; Python's str.strip() does not."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="Invalid skill name"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", [" pdf", "pdf ", "\tpdf", " pdf "])
    def test_build_command_skills_rejects_surrounding_whitespace(
        self, hostile_name: str
    ):
        """A padded rule can never match: the Skill tool trims before matching."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="whitespace"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", ["/pdf", "/myplugin:pdf"])
    def test_build_command_skills_rejects_leading_slash(self, hostile_name: str):
        """The session allowlist matches verbatim, so '/pdf' hides every skill."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="may not start with"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", ["lone\ud800surrogate", "\udc00leading"])
    def test_build_command_skills_rejects_surrogate_code_points(
        self, hostile_name: str
    ):
        """No CLI-discovered skill name contains a surrogate code point."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="surrogate"):
            transport._build_command()

    @pytest.mark.parametrize("hostile_name", ["nel\u0085end", "csi\u009bend"])
    def test_build_command_skills_rejects_c1_control_characters(
        self, hostile_name: str
    ):
        """Names containing C1 control characters raise ValueError."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[hostile_name]),
        )
        with pytest.raises(ValueError, match="Invalid skill name"):
            transport._build_command()

    @pytest.mark.parametrize(
        "benign_name",
        [
            "pdf-tools",
            "my_skill.v2",
            "myplugin:pdf",
            "skill with spaces",
            "dir\\sub",
            "日本語スキル",
        ],
    )
    def test_build_command_skills_accepts_ordinary_names(self, benign_name: str):
        """Ordinary names -- plugin-qualified, spaced, non-ASCII -- still work."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(skills=[benign_name]),
        )
        cmd = transport._build_command()
        assert flag_value(cmd, "--allowedTools") == f"Skill({benign_name})"

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
            assert flag_values(cmd, "--allowedTools") == []
        else:
            assert flag_value(cmd, "--allowedTools") == want_tools

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

        # Find the --mcp-config value
        mcp_config_value = two_argument_value(cmd, "--mcp-config")

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
        assert two_argument_value(cmd, "--mcp-config") == string_path

        # Test with Path object
        path_obj = Path("/path/to/mcp-config.json")
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=path_obj),
        )

        cmd = transport._build_command()
        # Path object gets converted to string, compare with str(path_obj)
        assert two_argument_value(cmd, "--mcp-config") == str(path_obj)

    def test_build_command_with_mcp_servers_as_json_string(self):
        """Test building CLI command with mcp_servers as JSON string."""
        json_config = '{"mcpServers": {"server": {"type": "stdio", "command": "test"}}}'
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(mcp_servers=json_config),
        )

        cmd = transport._build_command()
        assert two_argument_value(cmd, "--mcp-config") == json_config

    def test_build_command_mcp_servers_dict_with_env_and_headers(self):
        """A dict config goes out as two arguments, --mcp-config and then the
        JSON, with env and headers values only inside the JSON, even when a
        value begins with dashes."""
        mcp_servers = {
            "stdio-server": {
                "type": "stdio",
                "command": "/path/to/server",
                "env": {"SERVER_MODE": "--verbose"},
            },
            "http-server": {
                "type": "http",
                "url": "https://example.com/mcp",
                "headers": {"X-Server-Mode": "--verbose"},
            },
        }
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(mcp_servers=mcp_servers)
        )._build_command()

        value = two_argument_value(cmd, "--mcp-config")
        assert value.startswith("{")
        assert json.loads(value) == {"mcpServers": mcp_servers}
        assert [arg for arg in cmd if "SERVER_MODE" in arg] == [value]
        assert [arg for arg in cmd if "X-Server-Mode" in arg] == [value]

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
                # Mock the CLI process
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()  # Add async aclose method
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )

                await transport.connect()

                # cli_path is only executed once
                assert mock_open_process.call_count == 1

                call_kwargs = mock_open_process.call_args.kwargs
                assert "env" in call_kwargs
                env_passed = call_kwargs["env"]

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

                # Caller's entrypoint must win over the sdk-py default
                assert env_passed["CLAUDE_CODE_ENTRYPOINT"] == "custom-caller"

                # CLAUDE_AGENT_SDK_VERSION is still SDK-controlled
                assert "CLAUDE_AGENT_SDK_VERSION" in env_passed

        anyio.run(_test)

    @pytest.mark.parametrize(
        ("options_env", "ambient_env", "expected_value"),
        [
            ({}, {}, "1"),
            ({"CLAUDE_CODE_SDK_READS_SESSION_STATE": "0"}, {}, "0"),
            ({}, {"CLAUDE_CODE_SDK_READS_SESSION_STATE": "0"}, "0"),
            ({"claude_code_sdk_reads_session_state": "0"}, {}, None),
        ],
        ids=["unset", "caller_off", "ambient_off", "caller_other_case"],
    )
    def test_sdk_reads_session_state_unless_caller_chose(
        self, monkeypatch, options_env, ambient_env, expected_value
    ):
        """The transport asks for the sdk_host_only session_state_changed
        frames Query reads, unless the caller's options.env or the ambient
        environment names the variable, in any case (#1190). It never turns on
        CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS, which is the caller's own opt-in
        to seeing the frames."""
        monkeypatch.delenv("CLAUDE_CODE_SDK_READS_SESSION_STATE", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", raising=False)
        for key, value in ambient_env.items():
            monkeypatch.setenv(key, value)

        async def _test():
            options = make_options(env=options_env)

            with patch(
                "anyio.open_process", new_callable=AsyncMock
            ) as mock_open_process:
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]
                assert (
                    env_passed.get("CLAUDE_CODE_SDK_READS_SESSION_STATE")
                    == expected_value
                )
                assert "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS" not in env_passed

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()  # must not raise

                env_passed = mock_open_process.call_args.kwargs["env"]
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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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

                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(prompt="test", options=options)
                await transport.connect()  # must not raise CLIConnectionError

                env_passed = mock_open_process.call_args.kwargs["env"]
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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )
                await transport.connect()

                env_passed = mock_open_process.call_args.kwargs["env"]

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
                # Mock the CLI process
                mock_process = MagicMock()
                mock_process.stdout = MagicMock()
                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()  # Add async aclose method
                mock_process.stdin = mock_stdin
                mock_process.returncode = None

                mock_open_process.return_value = mock_process

                transport = SubprocessCLITransport(
                    prompt="test",
                    options=options,
                )

                await transport.connect()

                # cli_path is never executed as anyone but options.user
                assert [
                    call.kwargs["user"] for call in mock_open_process.call_args_list
                ] == [custom_user]

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
        settings_value = two_argument_value(cmd, "--settings")

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
        settings_value = two_argument_value(cmd, "--settings")

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
        assert two_argument_value(cmd, "--settings") == "/path/to/settings.json"

    def test_build_command_sandbox_reads_relative_settings_from_cwd(self, tmp_path):
        """A relative settings path resolves against options.cwd, not the process cwd."""
        import json

        (tmp_path / "settings.json").write_text(
            json.dumps({"permissions": {"deny": ["Bash"]}})
        )
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                settings="settings.json", sandbox={"enabled": True}, cwd=tmp_path
            ),
        )

        cmd = transport._build_command()

        parsed = json.loads(cmd[cmd.index("--settings") + 1])
        assert parsed == {
            "permissions": {"deny": ["Bash"]},
            "sandbox": {"enabled": True},
        }

    def test_build_command_sandbox_reads_absolute_settings_path(self, tmp_path):
        """An absolute settings path is read as given, whatever options.cwd is."""
        import json

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"permissions": {"deny": ["Bash"]}}))
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                settings=str(settings_file),
                sandbox={"enabled": True},
                cwd=tmp_path / "elsewhere",
            ),
        )

        cmd = transport._build_command()

        parsed = json.loads(cmd[cmd.index("--settings") + 1])
        assert parsed["permissions"] == {"deny": ["Bash"]}

    def test_build_command_sandbox_relative_settings_without_cwd(
        self, tmp_path, monkeypatch
    ):
        """Without options.cwd, a relative settings path uses the process cwd."""
        import json

        (tmp_path / "settings.json").write_text(json.dumps({"model": "m"}))
        monkeypatch.chdir(tmp_path)
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(settings="settings.json", sandbox={"enabled": True}),
        )

        cmd = transport._build_command()

        assert json.loads(cmd[cmd.index("--settings") + 1])["model"] == "m"

    @pytest.mark.parametrize(
        ("settings", "contents"),
        [
            ("missing.json", None),
            (".", None),
            ("settings.json", "{not json"),
            ("settings.json", "[1, 2]"),
            # Malformed inline JSON
            ('{"env": {"KEY": "secret-value",}}', None),
        ],
    )
    def test_build_command_sandbox_unloadable_settings_raise(
        self, tmp_path, settings, contents
    ):
        """Settings that can't be loaded raise instead of being dropped."""
        if contents is not None:
            (tmp_path / settings).write_text(contents)
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                settings=settings, sandbox={"enabled": True}, cwd=tmp_path
            ),
        )

        with pytest.raises(CLIConnectionError, match="settings") as exc_info:
            transport._build_command()
        # Inline settings can hold secrets
        assert "secret-value" not in str(exc_info.value)

    def test_build_command_sandbox_reads_settings_file_with_bom(self, tmp_path):
        """A settings file with a BOM loads, as it does for the CLI."""
        import json

        (tmp_path / "settings.json").write_text(
            '{"permissions": {"deny": ["Bash"]}}', encoding="utf-8-sig"
        )
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                settings="settings.json", sandbox={"enabled": True}, cwd=tmp_path
            ),
        )

        cmd = transport._build_command()

        merged = json.loads(cmd[cmd.index("--settings") + 1])
        assert merged["permissions"] == {"deny": ["Bash"]}

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

        settings_value = two_argument_value(cmd, "--settings")

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
        settings_value = two_argument_value(cmd, "--settings")

        parsed = json.loads(settings_value)
        network = parsed["sandbox"]["network"]

        assert network["allowUnixSockets"] == ["/tmp/ssh-agent.sock"]
        assert network["allowAllUnixSockets"] is False
        assert network["allowLocalBinding"] is True
        assert network["httpProxyPort"] == 8080
        assert network["socksProxyPort"] == 8081

    def test_sandbox_ignore_violations_passed_through(self):
        """Test ignoreViolations reaches --settings as a command-pattern map."""
        import json

        from claude_agent_sdk import SandboxSettings

        sandbox: SandboxSettings = {
            "enabled": True,
            "ignoreViolations": {
                "*": ["/etc/hosts"],
                "npm test": ["registry.npmjs.org"],
            },
        }

        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(sandbox=sandbox),
        )

        cmd = transport._build_command()
        settings_idx = cmd.index("--settings")
        parsed = json.loads(cmd[settings_idx + 1])

        assert parsed["sandbox"]["ignoreViolations"] == {
            "*": ["/etc/hosts"],
            "npm test": ["registry.npmjs.org"],
        }

    def test_build_command_with_tools_array(self):
        """Test building CLI command with tools as array of tool names."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools=["Read", "Edit", "Bash"]),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--tools") == "Read,Edit,Bash"

    def test_build_command_with_tools_empty_array(self):
        """Test building CLI command with tools as empty array (disables all tools)."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools=[]),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--tools") == ""

    def test_build_command_with_tools_preset(self):
        """Test building CLI command with tools preset."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(tools={"type": "preset", "preset": "claude_code"}),
        )

        cmd = transport._build_command()
        assert flag_value(cmd, "--tools") == "default"

    def test_build_command_without_tools(self):
        """Test building CLI command without tools option (default None)."""
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(),
        )

        cmd = transport._build_command()
        assert flag_values(cmd, "--tools") == []

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

                mock_exec.return_value = mock_process

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

                mock_exec.return_value = mock_process

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
                mock_process = MagicMock()
                mock_process.returncode = 0  # Already exited
                mock_process.terminate = MagicMock()
                mock_process.wait = AsyncMock()
                mock_process.stdout = MagicMock()
                mock_process.stderr = MagicMock()

                mock_stdin = MagicMock()
                mock_stdin.aclose = AsyncMock()
                mock_process.stdin = mock_stdin

                mock_exec.return_value = mock_process

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

    @staticmethod
    async def _read_frames(
        frames: list[dict[str, Any]], transport: SubprocessCLITransport
    ) -> MagicMock:
        """Feed stdout frames through the transport, returning its mocked logger."""
        stdout = [(json.dumps(frame) + "\n") for frame in frames]

        async def _chunks() -> AsyncIterator[str]:
            for chunk in stdout:
                yield chunk

        transport._process = MagicMock(wait=AsyncMock(return_value=0))
        transport._stdout_stream = _chunks()  # type: ignore[assignment]
        with patch(
            "claude_agent_sdk._internal.transport.subprocess_cli.logger"
        ) as mock_logger:
            assert [m async for m in transport.read_messages()] == frames
        return mock_logger

    def test_cli_too_old_to_report_its_version_warns_once(self):
        """A CLI that reports no version is logged once, with its path."""

        async def _test():
            transport = SubprocessCLITransport(prompt="test", options=make_options())
            frame = {"type": "system", "subtype": "init"}
            # The CLI sends an init message per turn.
            mock_logger = await self._read_frames([frame, frame], transport)

            mock_logger.warning.assert_called_once()
            args, _ = mock_logger.warning.call_args
            assert args[1:] == (DEFAULT_CLI_PATH, "2.0.0")

        anyio.run(_test)

    @pytest.mark.parametrize("version", ["2.0.20", "99.99.99"])
    def test_supported_cli_version_does_not_warn(self, version):
        async def _test():
            transport = SubprocessCLITransport(prompt="test", options=make_options())
            mock_logger = await self._read_frames(
                [{"type": "system", "subtype": "init", "claude_code_version": version}],
                transport,
            )
            mock_logger.warning.assert_not_called()

        anyio.run(_test)

    def test_version_warning_can_be_skipped(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

        async def _test():
            transport = SubprocessCLITransport(prompt="test", options=make_options())
            mock_logger = await self._read_frames(
                [{"type": "system", "subtype": "init"}],
                transport,
            )
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

            # The stream yields chunks, not lines: one read can carry several
            # lines, and the last one may have no trailing newline.
            async def mock_iter() -> AsyncIterator[str]:
                yield "line 1\nline 2\n"
                yield "line 3"

            transport._stderr_stream = mock_iter()  # type: ignore[assignment]
            await transport._handle_stderr()

            # All three lines must be delivered despite the first raise.
            assert received == ["line 1", "line 2", "line 3"]

        anyio.run(_test)

    def test_stderr_line_split_across_chunks_is_reassembled(self) -> None:
        """``options.stderr`` is documented to receive lines, but the stream
        yields chunks — a long line split at a read boundary must be delivered
        once, whole, rather than as two fragments with the seam whitespace
        rstripped off the first."""

        async def _test() -> None:
            received: list[str] = []

            transport = SubprocessCLITransport(
                prompt="x", options=ClaudeAgentOptions(stderr=received.append)
            )

            async def mock_iter() -> AsyncIterator[str]:
                yield "a warning that got "
                yield "split across two reads\nnext line\n"

            transport._stderr_stream = mock_iter()  # type: ignore[assignment]
            await transport._handle_stderr()

            assert received == [
                "a warning that got split across two reads",
                "next line",
            ]

        anyio.run(_test)

    def test_stderr_line_without_newline_is_flushed_at_buffer_limit(self) -> None:
        """A producer that never emits a newline must not grow the pending
        buffer without bound: once it passes ``max_buffer_size`` the partial
        line is flushed to the callback and the buffer resets."""

        async def _test() -> None:
            received: list[str] = []

            transport = SubprocessCLITransport(
                prompt="x",
                options=ClaudeAgentOptions(stderr=received.append, max_buffer_size=10),
            )

            async def mock_iter() -> AsyncIterator[str]:
                # 15 chars with no newline in sight, then a normal line.
                yield "aaaaa"
                yield "aaaaa"
                yield "aaaaa"
                yield "bbb\n"

            transport._stderr_stream = mock_iter()  # type: ignore[assignment]
            await transport._handle_stderr()

            # Flushed once the 15 chars passed the 10-char limit, rather than
            # buffering forever waiting for a newline.
            assert received == ["a" * 15, "bbb"]

        anyio.run(_test)

    def test_stderr_pending_line_is_flushed_when_task_is_cancelled(self) -> None:
        """close() cancels the stderr task, and cancellation is a BaseException
        that the reader's `except` clauses don't catch. A diagnostic written
        without a trailing newline before the CLI stalled must still reach the
        callback rather than being lost with the buffer."""

        async def _test() -> None:
            received: list[str] = []
            started = anyio.Event()

            async def mock_iter() -> AsyncIterator[str]:
                yield "Error: model overloaded"  # no trailing newline
                started.set()
                await anyio.sleep(60)  # the CLI stalls, holding the stream open

            transport = SubprocessCLITransport(
                prompt="x", options=ClaudeAgentOptions(stderr=received.append)
            )
            transport._stderr_stream = mock_iter()  # type: ignore[assignment]

            async with anyio.create_task_group() as tg:
                tg.start_soon(transport._handle_stderr)
                await started.wait()
                tg.cancel_scope.cancel()

            assert received == ["Error: model overloaded"]

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


def _mock_connect_process() -> MagicMock:
    """Build the process mock connect() awaits."""
    main_process = MagicMock()
    main_process.stdout = MagicMock()
    main_stdin = MagicMock()
    main_stdin.aclose = AsyncMock()
    main_process.stdin = main_stdin
    main_process.returncode = None
    return main_process


class TestWindowsBatchScriptRefusal:
    """connect() must never spawn a .bat/.cmd script on Windows.

    CreateProcess routes batch scripts through cmd.exe /c, and cmd.exe
    re-parses the whole command line, so every argument value would reach
    a shell. The refusal happens before connect() spawns anything.
    """

    _PLATFORM = "claude_agent_sdk._internal.transport.subprocess_cli.platform.system"

    def test_explicit_bat_cli_path_is_refused(self):
        async def _test():
            from claude_agent_sdk._errors import CLIConnectionError

            transport = SubprocessCLITransport(
                prompt="test",
                options=ClaudeAgentOptions(cli_path="C:\\tools\\claude.bat"),
            )

            with (
                patch(self._PLATFORM, return_value="Windows"),
                patch("anyio.open_process", new_callable=AsyncMock) as mock_open,
                pytest.raises(CLIConnectionError, match="batch script"),
            ):
                await transport.connect()

            assert mock_open.call_count == 0

        anyio.run(_test)

    @pytest.mark.parametrize(
        "cli_path",
        [
            "C:\\tools\\claude.cmd.",
            "C:\\tools\\claude.CMD ",
            "C:\\tools\\claude.cmd:stream",
            "C:\\tools\\.cmd",
            "C:claude.cmd",
            "C:/tools/claude.cmd",
            "\\\\server\\share\\claude.cmd",
            "C:\\tools\\claude.cmd\\.",
            "C:\\tools\\claude.cmd\\x\\..",
            "C:\\tools\\claude.cmd\\x\\.. ",
            "C:\\tools\\claude.cmd\\x\\.. .",
            "C:\\tools\\claude.cmd\\\\.",
            "C:/tools/claude.cmd//x/..",
            "C:\\tools\\claude.cmd\\",
            "C:\\tools\\claude.cmd\\...",
            "C:\\tools\\claude.cmd\\....",
            "C:\\tools\\claude:evil.cmd",
            "C:\\tools\\claude.exe:evil.cmd",
            ":claude.cmd",
            # A middle dots/spaces-only component is a literal name on Win32
            # (trailing-dot trimming applies to the final segment only), so
            # a following ".." pops that literal and lands on claude.cmd.
            "C:\\tools\\claude.cmd\\...\\..",
            "C:\\tools\\claude.cmd\\. .\\..",
            "C:\\tools\\claude.cmd\\ \\..",
            "C:\\tools\\claude.cmd\\.. \\..",
        ],
    )
    def test_suffix_tricks_are_refused(self, cli_path: str):
        async def _test():
            from claude_agent_sdk._errors import CLIConnectionError

            transport = SubprocessCLITransport(
                prompt="test", options=ClaudeAgentOptions(cli_path=cli_path)
            )

            with (
                patch(self._PLATFORM, return_value="Windows"),
                patch("anyio.open_process", new_callable=AsyncMock) as mock_open,
                pytest.raises(CLIConnectionError, match="batch script"),
            ):
                await transport.connect()

            assert mock_open.call_count == 0

        anyio.run(_test)

    def test_native_exe_is_allowed_on_windows(self):
        async def _test():
            transport = SubprocessCLITransport(
                prompt="test",
                options=ClaudeAgentOptions(
                    cli_path="C:\\Users\\u\\.local\\bin\\claude.EXE"
                ),
            )
            main_process = _mock_connect_process()

            with (
                patch(self._PLATFORM, return_value="Windows"),
                patch("anyio.open_process", new_callable=AsyncMock) as mock_open,
            ):
                mock_open.return_value = main_process
                await transport.connect()

            assert mock_open.call_count == 1

        anyio.run(_test)

    @pytest.mark.parametrize("system", ["Linux", "Darwin"])
    def test_posix_platforms_are_unchanged(self, system: str):
        async def _test():
            transport = SubprocessCLITransport(
                prompt="test", options=ClaudeAgentOptions()
            )
            main_process = _mock_connect_process()

            with (
                patch(self._PLATFORM, return_value=system),
                patch.object(
                    SubprocessCLITransport, "_find_bundled_cli", return_value=None
                ),
                patch(
                    "claude_agent_sdk._internal.transport.subprocess_cli.shutil.which",
                    return_value="/usr/local/bin/claude",
                ) as mock_which,
                patch("anyio.open_process", new_callable=AsyncMock) as mock_open,
            ):
                mock_open.return_value = main_process
                await transport.connect()

            assert mock_which.call_count == 1
            assert mock_which.call_args.args == ("claude",)
            assert mock_open.call_count == 1
            assert mock_open.call_args.args[0][0] == "/usr/local/bin/claude"

        anyio.run(_test)

    def test_guard_is_a_no_op_off_windows(self):
        with patch(self._PLATFORM, return_value="Linux"):
            SubprocessCLITransport._reject_windows_batch_cli("/odd/claude.cmd")

    def _not_found_message(self, system: str) -> str:
        transport = SubprocessCLITransport(prompt="test", options=ClaudeAgentOptions())
        with (
            patch(self._PLATFORM, return_value=system),
            patch.object(
                SubprocessCLITransport, "_find_bundled_cli", return_value=None
            ),
            patch.dict(os.environ, {"PATH": ""}),
            patch("pathlib.Path.exists", return_value=False),
            pytest.raises(CLINotFoundError) as exc_info,
        ):
            transport._find_cli()
        return str(exc_info.value)

    def test_not_found_message_on_windows_recommends_native_exe(self):
        # The npm route yields a claude.cmd shim that connect() refuses, so
        # the Windows message must lead with the native claude.exe install.
        message = self._not_found_message("Windows")
        assert "install.ps1" in message
        assert "claude.exe" in message
        assert message.index("install.ps1") < message.index("npm")
        assert "refuses" in message

    def test_not_found_message_off_windows_is_unchanged(self):
        message = self._not_found_message("Linux")
        assert message.startswith(
            "Claude Code not found. Install with:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
        )
        assert "install.ps1" not in message


class TestWindowsCliLookup:
    """On Windows, _find_cli() only finds claude.exe in absolute directories."""

    @pytest.fixture
    def find_cli(self, tmp_path, monkeypatch):
        """Run _find_cli() as on Windows, from tmp_path with an empty home."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        monkeypatch.setattr(
            "claude_agent_sdk._internal.transport.subprocess_cli.platform.system",
            lambda: "Windows",
        )
        monkeypatch.setattr(
            SubprocessCLITransport, "_find_bundled_cli", lambda self: None
        )

        def _find_cli(*path: object) -> str:
            monkeypatch.setenv("PATH", os.pathsep.join(map(str, path)))
            return SubprocessCLITransport(
                prompt="test", options=ClaudeAgentOptions()
            )._find_cli()

        return _find_cli

    @staticmethod
    def _add(directory, name="claude.exe"):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"")
        path.chmod(0o755)
        return path

    def test_finds_native_exe_on_path(self, find_cli, tmp_path):
        exe = self._add(tmp_path / "bin")
        assert find_cli(tmp_path / "missing", tmp_path / "bin") == str(exe)

    def test_working_directory_is_not_searched(self, find_cli, tmp_path):
        self._add(tmp_path)
        self._add(tmp_path / "tools")
        exe = self._add(tmp_path / "bin")
        # "" and "." name the working directory, "tools" a directory under it
        assert find_cli("", ".", "tools", tmp_path / "bin") == str(exe)

    def test_only_working_directory_hits_is_not_found(self, find_cli, tmp_path):
        self._add(tmp_path)
        with pytest.raises(CLINotFoundError):
            find_cli("", ".")

    def test_shim_and_wrapper_do_not_shadow_native_exe(self, find_cli, tmp_path):
        self._add(tmp_path / "npm", "claude.cmd")
        self._add(tmp_path / "npm", "claude.exe.cmd")
        self._add(tmp_path / "wsl", "claude")
        exe = self._add(tmp_path / "bin")
        assert find_cli(tmp_path / "npm", tmp_path / "wsl", tmp_path / "bin") == str(
            exe
        )

    def test_shim_only_machine_gets_the_native_install_message(
        self, find_cli, tmp_path
    ):
        self._add(tmp_path / "npm", "claude.cmd")
        with pytest.raises(CLINotFoundError, match="claude.cmd shim"):
            find_cli(tmp_path / "npm")

    def test_path_hit_does_not_need_a_home_directory(
        self, find_cli, tmp_path, monkeypatch
    ):
        def _no_home() -> None:
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr("pathlib.Path.home", _no_home)
        exe = self._add(tmp_path / "bin")
        assert find_cli(tmp_path / "bin") == str(exe)

    def test_native_installer_location_is_the_fallback(self, find_cli, tmp_path):
        exe = self._add(tmp_path / "home/.local/bin")
        # Not the POSIX-shaped name beside it
        self._add(tmp_path / "home/.local/bin", "claude")
        assert find_cli(tmp_path / "missing") == str(exe)


class TestExtraArgsValueBinding:
    """extra_args uses the equals form for dash-leading values so the value
    binds to its flag instead of parsing as a separate CLI flag."""

    def test_dash_leading_value_uses_equals_form(self):
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(extra_args={"future-flag": "--evil"}),
        )
        cmd = transport._build_command()
        assert "--future-flag=--evil" in cmd
        assert "--evil" not in cmd
        assert "--future-flag" not in cmd

    def test_ordinary_value_keeps_two_token_form(self):
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                extra_args={"future-flag": "plain", "bool-flag": None}
            ),
        )
        cmd = transport._build_command()
        idx = cmd.index("--future-flag")
        assert cmd[idx + 1] == "plain"
        assert "--bool-flag" in cmd


# Values that begin with dashes and are spelled like real CLI flags.
_DASH_LEADING_VALUES = ["--help", "-p", "--version", "--model=other-model"]

# (option overrides for a given value, the flag that must carry the value)
_VALUE_OPTIONS = [
    (lambda v: {"system_prompt": v}, "--system-prompt"),
    (lambda v: {"system_prompt": {"type": "custom", "prompt": v}}, "--system-prompt"),
    (
        lambda v: {
            "system_prompt": {"type": "preset", "preset": "claude_code", "append": v}
        },
        "--append-system-prompt",
    ),
    (lambda v: {"system_prompt": {"type": "file", "path": v}}, "--system-prompt-file"),
    (lambda v: {"tools": [v]}, "--tools"),
    (lambda v: {"allowed_tools": [v]}, "--allowedTools"),
    (lambda v: {"disallowed_tools": [v]}, "--disallowedTools"),
    (lambda v: {"model": v}, "--model"),
    (lambda v: {"fallback_model": v}, "--fallback-model"),
    (lambda v: {"betas": [v]}, "--betas"),
    (lambda v: {"permission_prompt_tool_name": v}, "--permission-prompt-tool"),
    (lambda v: {"permission_mode": v}, "--permission-mode"),
    (lambda v: {"add_dirs": [v]}, "--add-dir"),
    (lambda v: {"mcp_servers": v}, "--mcp-config"),
    (lambda v: {"plugins": [{"type": "local", "path": v}]}, "--plugin-dir"),
    (
        lambda v: {"thinking": {"type": "adaptive", "display": v}},
        "--thinking-display",
    ),
    (lambda v: {"effort": v}, "--effort"),
    (lambda v: {"resume": v}, "--resume"),
    (lambda v: {"session_id": v}, "--session-id"),
    (lambda v: {"resume": "abc", "resume_session_at": v}, "--resume-session-at"),
    (lambda v: {"resume": "abc", "resume_drops_turn": v}, "--resume-drops-turn"),
    (lambda v: {"setting_sources": [v]}, "--setting-sources"),
    (lambda v: {"extra_args": {"future-flag": v}}, "--future-flag"),
]


class _TextEnum(str, Enum):
    """Python 3.11 and later format a member of this as _TextEnum.DASHED."""

    DASHED = "--some-text"
    PLAIN = "some-text"


class TestDashLeadingOptionValues:
    """A value that begins with dashes reaches the CLI attached to its flag.

    Such a value -- a system prompt, a model name or a path that happens to
    look like a CLI flag -- must be read by the CLI as that option's value.
    Passing it as part of one --flag=value argument guarantees this: the
    value is never an argument of its own.
    """

    @pytest.mark.parametrize("value", _DASH_LEADING_VALUES)
    @pytest.mark.parametrize(
        ("overrides", "flag"),
        _VALUE_OPTIONS,
        ids=[f"{i:02d}{flag}" for i, (_, flag) in enumerate(_VALUE_OPTIONS)],
    )
    def test_value_is_attached_to_its_flag(self, overrides, flag, value):
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(**overrides(value))
        )
        cmd = transport._build_command()
        assert flag_value(cmd, flag) == value
        assert value not in cmd

    def test_every_value_is_attached_when_all_options_are_set(self):
        """With every string-valued option set to a value that begins with
        dashes, every argument after the executable begins with "--" except
        the two fixed stream-json format values and the --settings path,
        which gets a "./" prefix."""
        value = "--help"
        options = make_options(
            system_prompt=value,
            tools=[value],
            allowed_tools=[value],
            disallowed_tools=[value],
            max_turns=3,
            max_budget_usd=1.5,
            task_budget={"total": 1000},
            model=value,
            fallback_model=value,
            betas=[value],
            permission_prompt_tool_name=value,
            permission_mode=value,
            resume=value,
            session_id=value,
            settings=value,
            add_dirs=[value, value],
            mcp_servers=value,
            resume_session_at=value,
            resume_drops_turn=value,
            setting_sources=[value],
            plugins=[{"type": "local", "path": value}],
            extra_args={"future-flag": value, "bool-flag": None},
            thinking={"type": "enabled", "budget_tokens": 2000, "display": value},
            effort=value,
            output_format={"type": "json_schema", "schema": {"type": "object"}},
        )
        cmd = SubprocessCLITransport(prompt="test", options=options)._build_command()

        args = cmd[1:]
        assert value not in args
        standalone = [a for a in args if not a.startswith("--")]
        assert standalone == ["stream-json", "./" + value, "stream-json"]
        assert args[args.index("--output-format") + 1] == "stream-json"
        assert args[args.index("--input-format") + 1] == "stream-json"
        assert two_argument_value(args, "--settings") == "./" + value
        # Every value-taking option above was actually passed.
        assert sorted(a.split("=", 1)[0] for a in args if "=" in a) == sorted(
            [
                "--system-prompt",
                "--tools",
                "--allowedTools",
                "--disallowedTools",
                "--max-turns",
                "--max-budget-usd",
                "--task-budget",
                "--model",
                "--fallback-model",
                "--betas",
                "--permission-prompt-tool",
                "--permission-mode",
                "--resume",
                "--session-id",
                "--add-dir",
                "--add-dir",
                "--mcp-config",
                "--resume-session-at",
                "--resume-drops-turn",
                "--setting-sources",
                "--plugin-dir",
                "--future-flag",
                "--max-thinking-tokens",
                "--thinking-display",
                "--effort",
                "--json-schema",
            ]
        )


class TestValueTypes:
    """A value reaches the CLI as the text it would have as its own argument."""

    @pytest.mark.parametrize(
        ("overrides", "flag"),
        _VALUE_OPTIONS,
        ids=[f"{i:02d}{flag}" for i, (_, flag) in enumerate(_VALUE_OPTIONS)],
    )
    def test_str_enum_member_keeps_its_text(self, overrides, flag):
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(**overrides(_TextEnum.DASHED))
        )._build_command()
        assert flag_value(cmd, flag) == "--some-text"

    def test_str_enum_member_keeps_its_text_as_a_second_argument(self):
        cmd = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                settings=_TextEnum.PLAIN,
                mcp_servers=_TextEnum.PLAIN,
                extra_args={"future-flag": _TextEnum.PLAIN},
            ),
        )._build_command()
        sent = [os.fsencode(argument) for argument in cmd]
        for flag in ("--settings", "--mcp-config", "--future-flag"):
            assert sent[sent.index(flag.encode()) + 1] == b"some-text"

    def test_path_and_uuid_values_are_converted(self):
        session_id = uuid.UUID(int=1)
        cmd = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                session_id=session_id,
                add_dirs=[Path("some/dir")],
                plugins=[{"type": "local", "path": Path("some/plugin")}],
                mcp_servers=Path("some/mcp.json"),
            ),
        )._build_command()
        assert flag_value(cmd, "--session-id") == str(session_id)
        assert flag_value(cmd, "--add-dir") == str(Path("some/dir"))
        assert flag_value(cmd, "--plugin-dir") == str(Path("some/plugin"))
        assert two_argument_value(cmd, "--mcp-config") == str(Path("some/mcp.json"))

    def test_bytes_and_path_like_values_give_their_file_system_text(self):
        class PathLike:
            def __fspath__(self):
                return "file/system/text"

            def __str__(self):
                return "other text"

        cmd = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                model=b"some-model",
                plugins=[{"type": "local", "path": PathLike()}],
                settings=PathLike(),
            ),
        )._build_command()
        assert flag_value(cmd, "--model") == "some-model"
        assert flag_value(cmd, "--plugin-dir") == "file/system/text"
        assert two_argument_value(cmd, "--settings") == "file/system/text"

    @pytest.mark.parametrize(
        ("overrides", "flag"),
        [
            (
                {"system_prompt": {"type": "preset", "append": None}},
                "--append-system-prompt",
            ),
            ({"system_prompt": {"type": "custom", "prompt": None}}, "--system-prompt"),
            ({"system_prompt": {"type": "file", "path": None}}, "--system-prompt-file"),
            ({"plugins": [{"type": "local", "path": None}]}, "--plugin-dir"),
            ({"add_dirs": [None]}, "--add-dir"),
            (
                {"thinking": {"type": "adaptive", "display": None}},
                "--thinking-display",
            ),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_none_as_a_value_raises_and_names_the_flag(self, overrides, flag):
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(**overrides)
        )
        with pytest.raises(CLIConnectionError, match=flag):
            transport._build_command()

    def test_options_left_unset_send_no_value(self):
        cmd = SubprocessCLITransport(
            prompt="test",
            options=make_options(
                model=None,
                fallback_model=None,
                effort=None,
                settings=None,
                resume=None,
                resume_drops_turn=None,
                extra_args={"bool-flag": None},
            ),
        )._build_command()
        assert "None" not in repr(cmd)
        assert "--bool-flag" in cmd


class TestSettingsArgumentForm:
    """--settings is always two arguments.

    Claude Code 2.0.69 and earlier read --settings before parsing options,
    and only as "--settings <value>"; "--settings=<value>" would be ignored
    there. A value that begins with "-" can only be a file path, and gets a
    "./" prefix so that it cannot be read as an option of its own.
    """

    @pytest.mark.parametrize(
        "settings",
        [
            '{"permissions": {"deny": ["Bash"]}}',
            "/path/to/settings.json",
            "relative/settings.json",
        ],
    )
    def test_value_follows_the_flag(self, settings):
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(settings=settings)
        )._build_command()
        assert two_argument_value(cmd, "--settings") == settings

    def test_merged_sandbox_settings_follow_the_flag(self):
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(sandbox={"enabled": True})
        )._build_command()
        assert json.loads(two_argument_value(cmd, "--settings")) == {
            "sandbox": {"enabled": True}
        }

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            # str() of an absolute Path uses the platform's separators.
            (Path("/path/to/settings.json"), str(Path("/path/to/settings.json"))),
            (Path("-settings.json"), "./-settings.json"),
        ],
    )
    def test_path_object_is_passed_as_a_string(self, path, expected):
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(settings=path)
        )._build_command()
        assert two_argument_value(cmd, "--settings") == expected

    @pytest.mark.parametrize("path", ["-settings.json", *_DASH_LEADING_VALUES])
    def test_dash_leading_path_gets_a_dot_slash_prefix(self, path):
        cmd = SubprocessCLITransport(
            prompt="test", options=make_options(settings=path)
        )._build_command()
        assert two_argument_value(cmd, "--settings") == "./" + path
        assert path not in cmd


def test_ordinary_values_are_attached_except_two_argument_options():
    """With ordinary values, the only arguments that stand on their own are
    the two fixed stream-json format values and the values of --settings,
    --mcp-config and extra_args."""
    settings = '{"permissions": {"deny": ["Bash"]}}'
    mcp_servers = {"server": {"type": "stdio", "command": "/path/to/server"}}
    mcp_config = json.dumps({"mcpServers": mcp_servers})
    options = make_options(
        system_prompt="You are helpful.",
        tools=["Read", "Bash"],
        allowed_tools=["Read"],
        disallowed_tools=["Bash"],
        max_turns=3,
        model="claude-sonnet-4-5",
        fallback_model="claude-haiku-4-5",
        permission_mode="default",
        settings=settings,
        add_dirs=["/tmp/a", "/tmp/b"],
        mcp_servers=mcp_servers,
        setting_sources=["user", "project"],
        extra_args={"future-flag": "plain", "bool-flag": None},
        effort="high",
    )
    cmd = SubprocessCLITransport(prompt="test", options=options)._build_command()

    standalone = [a for a in cmd[1:] if not a.startswith("--")]
    assert standalone == ["stream-json", settings, mcp_config, "plain", "stream-json"]
    assert cmd[cmd.index("--settings") + 1] == settings
    assert two_argument_value(cmd, "--mcp-config") == mcp_config
    assert cmd[cmd.index("--future-flag") + 1] == "plain"
    assert flag_value(cmd, "--model") == "claude-sonnet-4-5"
    assert flag_values(cmd, "--add-dir") == ["/tmp/a", "/tmp/b"]
    assert flag_value(cmd, "--setting-sources") == "user,project"


class TestWindowsCmdMetacharacterRejection:
    """Defense in depth: resume/session_id reject cmd.exe metacharacters on
    Windows so those values stay inert even if a cmd.exe hop reappears."""

    _PLATFORM = "claude_agent_sdk._internal.transport.subprocess_cli.platform.system"

    @pytest.mark.parametrize(
        "value",
        [
            "x&calc",
            "x|whoami",
            "x<in",
            "x>out",
            "x^y",
            "x%PATH%y",
            "x!VAR!y",
            'x"y',
            "x\ny",
            "x\ry",
        ],
    )
    def test_bad_resume_values_raise_on_windows(self, value: str):
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(resume=value)
        )
        with (
            patch(self._PLATFORM, return_value="Windows"),
            pytest.raises(ValueError, match="unsafe"),
        ):
            transport._build_command()

    def test_non_str_values_are_checked_as_text_on_windows(self):
        session_id = uuid.UUID(int=1)
        with patch(self._PLATFORM, return_value="Windows"):
            cmd = SubprocessCLITransport(
                prompt="test", options=make_options(session_id=session_id)
            )._build_command()
            assert flag_value(cmd, "--session-id") == str(session_id)
            with pytest.raises(ValueError, match="unsafe"):
                SubprocessCLITransport(
                    prompt="test", options=make_options(resume=b"x&calc")
                )._build_command()

    def test_bad_session_id_raises_on_windows(self):
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(session_id="x&ver")
        )
        with (
            patch(self._PLATFORM, return_value="Windows"),
            pytest.raises(ValueError, match="session_id"),
        ):
            transport._build_command()

    @pytest.mark.parametrize("option", ["resume_session_at", "resume_drops_turn"])
    def test_bad_truncating_resume_values_raise_on_windows(self, option: str):
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(resume="abc", **{option: "x&calc"}),
        )
        with (
            patch(self._PLATFORM, return_value="Windows"),
            pytest.raises(ValueError, match=option),
        ):
            transport._build_command()

    def test_ordinary_title_is_accepted_on_windows(self):
        title = "My project - daily notes (v2) #3"
        transport = SubprocessCLITransport(
            prompt="test", options=make_options(resume=title)
        )
        with patch(self._PLATFORM, return_value="Windows"):
            cmd = transport._build_command()
        assert f"--resume={title}" in cmd

    def test_posix_allows_metacharacters(self):
        transport = SubprocessCLITransport(
            prompt="test",
            options=make_options(resume="title & % | notes", session_id="a>b"),
        )
        with patch(self._PLATFORM, return_value="Linux"):
            cmd = transport._build_command()
        assert "--resume=title & % | notes" in cmd
        assert "--session-id=a>b" in cmd
