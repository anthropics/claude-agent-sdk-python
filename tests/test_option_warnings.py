"""Tests for the can_use_tool shadowing warning.

Covers CanUseToolShadowedWarning: emitted at connect()/query() time when
can_use_tool is set but allowed_tools / permission_mode auto-approve tool
calls before the callback would be consulted. Mirrors the TypeScript SDK's
canUseToolShadowing tests, targeting the message builder and the emit
function directly.
"""

import json
import warnings
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest

from claude_agent_sdk import (
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    query,
)
from claude_agent_sdk.types import (
    _get_can_use_tool_shadowed_warning,
    _warn_if_can_use_tool_shadowed,
    _whole_tool_allowed,
)


async def _can_use_tool(tool_name, input_data, context):
    return PermissionResultAllow()


class TestWholeToolAllowed:
    """allowed_tools entries that allow a whole tool, mirroring the CLI parser."""

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            # No specifier -- a plain tool-wide allow.
            ("Read", "Read"),
            ("mcp__server__tool", "mcp__server__tool"),
            # Empty / lone-wildcard specifiers collapse to a tool-wide allow.
            ("Read(*)", "Read"),
            ("Read()", "Read"),
            ("mcp__server__tool(*)", "mcp__server__tool"),
            # A real specifier only allows matching invocations.
            ("Bash(ls:*)", None),
            ("Bash(git log:*)", None),
            ("Bash(*.py)", None),
            # Blank entries match no tool.
            ("", None),
            ("   ", None),
            # Malformed entries fall back to the whole string as a tool name in
            # the CLI, so they match nothing.
            ("Bash(ls:*", None),
            ("Bash(ls)x", None),
            ("(foo)", None),
            # These two pin the guards: "(*)" has no tool name before the paren
            # (dropping the open_index check would emit an empty tool name), and
            # "Read(*x" never closes (dropping the endswith check would emit a
            # false-positive "Read"). Both would otherwise slip past the content
            # check, which only sees "*" and "*x".
            ("(*)", None),
            ("Read(*x", None),
        ],
    )
    def test_whole_tool_allowed(self, entry, expected):
        assert _whole_tool_allowed(entry) == expected


class TestGetCanUseToolShadowedWarning:
    """Direct tests of the message builder (mirrors TS getCanUseToolShadowedWarning)."""

    def test_bypass_permissions_message(self):
        message = _get_can_use_tool_shadowed_warning("bypassPermissions", [])
        assert message is not None
        assert "bypassPermissions" in message
        assert "PreToolUse" in message

    def test_bare_entries_message(self):
        message = _get_can_use_tool_shadowed_warning(
            None, ["Read", "mcp__server__tool", "Bash(ls:*)"]
        )
        assert message is not None
        assert "Read, mcp__server__tool" in message
        assert "Bash(ls:*)" not in message
        assert "PreToolUse" in message
        assert "settings files" in message

    def test_bypass_permissions_takes_precedence_over_bare_entries(self):
        message = _get_can_use_tool_shadowed_warning(
            "bypassPermissions", ["Read", "Write"]
        )
        assert message is not None
        assert "bypassPermissions" in message
        assert "Read" not in message
        assert "Write" not in message

    def test_preserves_allowed_tools_order(self):
        message = _get_can_use_tool_shadowed_warning(None, ["Write", "Read"])
        assert message is not None
        assert "Write, Read" in message

    def test_accept_edits_without_bare_entries_returns_none(self):
        assert _get_can_use_tool_shadowed_warning("acceptEdits", []) is None

    def test_accept_edits_still_reports_bare_entries(self):
        """acceptEdits alone is silent, but bare entries shadow in any mode."""
        message = _get_can_use_tool_shadowed_warning("acceptEdits", ["Read"])
        assert message is not None
        assert "Read" in message
        assert "bypassPermissions" not in message

    def test_wildcard_and_empty_specifiers_are_whole_tool_allows(self):
        """Tool(*) and Tool() are tool-wide allow rules, exactly like bare Tool."""
        message = _get_can_use_tool_shadowed_warning(None, ["Read(*)", "Write()"])
        assert message is not None
        assert "invoked for: Read, Write." in message

    def test_blank_entries_never_reach_the_message(self):
        """A whitespace-only entry must not render as `invoked for:    .`"""
        message = _get_can_use_tool_shadowed_warning(None, ["   ", "Read"])
        assert message is not None
        assert "invoked for: Read." in message

    def test_entries_resolving_to_the_same_tool_are_reported_once(self):
        """["Read", "Read()"] both resolve to Read -- don't say "Read, Read."."""
        message = _get_can_use_tool_shadowed_warning(
            None, ["Read", "Read()", "Read(*)"]
        )
        assert message is not None
        assert "invoked for: Read." in message

    def test_dedup_preserves_first_seen_order(self):
        message = _get_can_use_tool_shadowed_warning(None, ["Write", "Read", "Write()"])
        assert message is not None
        assert "invoked for: Write, Read." in message

    def test_specifier_and_empty_entries_return_none(self):
        assert (
            _get_can_use_tool_shadowed_warning(
                None,
                ["Bash(ls:*)", "Bash(git log:*)", "mcp__server__tool(param:value)", ""],
            )
            is None
        )

    def test_empty_allowed_tools_default_mode_returns_none(self):
        assert _get_can_use_tool_shadowed_warning(None, []) is None


class TestSkillsShadowing:
    """skills="all" makes the transport inject a bare "Skill" allow rule."""

    def test_skills_all_shadows_the_skill_tool(self):
        options = ClaudeAgentOptions(can_use_tool=_can_use_tool, skills="all")
        with pytest.warns(CanUseToolShadowedWarning, match="invoked for: Skill"):
            _warn_if_can_use_tool_shadowed(options)

    def test_named_skills_do_not_shadow(self):
        """skills=[names] injects Skill(name) specifiers, which are not whole-tool."""
        options = ClaudeAgentOptions(can_use_tool=_can_use_tool, skills=["reviewer"])
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            _warn_if_can_use_tool_shadowed(options)

    def test_skills_all_does_not_duplicate_explicit_skill_entry(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool, skills="all", allowed_tools=["Skill"]
        )
        with pytest.warns(CanUseToolShadowedWarning) as record:
            _warn_if_can_use_tool_shadowed(options)
        assert "invoked for: Skill." in str(record[0].message)

    def test_skills_all_with_wildcard_skill_entry_reports_skill_once(self):
        """skills="all" appends bare Skill even if Skill(*) is present; dedup it."""
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool, skills="all", allowed_tools=["Skill(*)"]
        )
        with pytest.warns(CanUseToolShadowedWarning) as record:
            _warn_if_can_use_tool_shadowed(options)
        assert "invoked for: Skill." in str(record[0].message)

    def test_skills_all_leaves_caller_allowed_tools_untouched(self):
        """The injected entry must not mutate the caller's list."""
        allowed = ["Read"]
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool, skills="all", allowed_tools=allowed
        )
        with pytest.warns(CanUseToolShadowedWarning, match="Read, Skill"):
            _warn_if_can_use_tool_shadowed(options)
        assert allowed == ["Read"]


class TestWarnIfCanUseToolShadowed:
    """Direct tests of the emit function (mirrors TS warnIfCanUseToolShadowed)."""

    def test_warns_when_callback_set_with_bypass(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            permission_mode="bypassPermissions",
        )
        with pytest.warns(CanUseToolShadowedWarning, match="bypassPermissions"):
            _warn_if_can_use_tool_shadowed(options)

    def test_no_warning_without_callback(self):
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Bash"],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            _warn_if_can_use_tool_shadowed(options)

    def test_warns_when_callback_set_with_wildcard_specifier(self):
        """Regression: Read(*) fully shadows the callback and must not stay silent."""
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            allowed_tools=["Read(*)"],
        )
        with pytest.warns(CanUseToolShadowedWarning, match="invoked for: Read"):
            _warn_if_can_use_tool_shadowed(options)

    def test_no_warning_for_accept_edits_with_specifier_entries(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            permission_mode="acceptEdits",
            allowed_tools=["Bash(ls:*)"],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            _warn_if_can_use_tool_shadowed(options)

    def test_repeat_shadowed_configs_dedupe_under_default_filter(self):
        """Python's default filter shows one warning per (message, call site).

        Two identical shadowed configs warn once; a config naming a different
        tool produces a different message and warns again. The emit itself is
        unconditional -- `simplefilter("always")` sees every occurrence.
        """
        same_a = ClaudeAgentOptions(can_use_tool=_can_use_tool, allowed_tools=["Read"])
        same_b = ClaudeAgentOptions(can_use_tool=_can_use_tool, allowed_tools=["Read"])
        different = ClaudeAgentOptions(
            can_use_tool=_can_use_tool, allowed_tools=["Write"]
        )

        def emit(options):  # single call site, so a single warning registry key
            _warn_if_can_use_tool_shadowed(options)

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("default")
            emit(same_a)
            emit(same_b)
            emit(different)
        assert len(record) == 2
        assert "invoked for: Read." in str(record[0].message)
        assert "invoked for: Write." in str(record[1].message)

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            emit(same_a)
            emit(same_b)
        assert len(record) == 2

    def test_warning_is_ignorable_via_filterwarnings(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            allowed_tools=["Read"],
        )
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)
            _warn_if_can_use_tool_shadowed(options)
        assert record == []


class TestConstructionIsSilent:
    """Options construction and cloning never emit the warning."""

    def test_constructing_shadowed_options_emits_no_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            ClaudeAgentOptions(
                can_use_tool=_can_use_tool,
                permission_mode="bypassPermissions",
                allowed_tools=["Read"],
            )

    def test_replace_on_shadowed_options_emits_no_warning(self):
        """Regression: internal replace() at connect time must not re-warn."""
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            allowed_tools=["Read"],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            replace(options, permission_prompt_tool_name="stdio")


def _create_mock_transport():
    """Mock transport that answers every control request with success."""
    transport = AsyncMock()
    transport.connect = AsyncMock()
    transport.close = AsyncMock()
    transport.end_input = AsyncMock()
    transport.is_ready = Mock(return_value=True)

    written_messages: list[str] = []

    async def mock_write(data):
        written_messages.append(data)

    transport.write = AsyncMock(side_effect=mock_write)

    async def control_protocol_generator():
        last_check = 0
        timeout_counter = 0
        while timeout_counter < 200:  # Avoid infinite loop
            await anyio.sleep(0.01)
            timeout_counter += 1
            for msg_str in written_messages[last_check:]:
                try:
                    msg = json.loads(msg_str.strip())
                    if msg.get("type") == "control_request":
                        yield {
                            "type": "control_response",
                            "response": {
                                "request_id": msg.get("request_id"),
                                "subtype": "success",
                                "response": {},
                            },
                        }
                except (json.JSONDecodeError, KeyError, AttributeError):
                    pass
            last_check = len(written_messages)

    transport.read_messages = control_protocol_generator
    return transport


class TestConnectEmitsWarning:
    """connect() emits the warning exactly once for a shadowed config."""

    @pytest.mark.anyio
    async def test_connect_warns_once_for_shadowed_config(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            allowed_tools=["Read"],
        )
        client = ClaudeSDKClient(options, transport=_create_mock_transport())
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            try:
                await client.connect()
            finally:
                await client.disconnect()
        shadow_warnings = [
            w for w in record if issubclass(w.category, CanUseToolShadowedWarning)
        ]
        assert len(shadow_warnings) == 1
        assert "Read" in str(shadow_warnings[0].message)

    @pytest.mark.anyio
    async def test_connect_silent_for_non_shadowed_config(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            allowed_tools=["Bash(ls:*)"],
        )
        client = ClaudeSDKClient(options, transport=_create_mock_transport())
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            try:
                await client.connect()
            finally:
                await client.disconnect()


class _StopBeforeConnectError(Exception):
    """Sentinel raised from the transport to end query() early."""


def _failing_transport():
    """Transport that aborts at connect(), after options validation has run."""
    transport = AsyncMock()
    transport.connect = AsyncMock(side_effect=_StopBeforeConnectError)
    return transport


async def _prompt_stream():
    yield {"type": "user", "message": {"role": "user", "content": "hi"}}


class TestQueryEmitsWarning:
    """query() -- the other entry point -- warns before the transport connects."""

    @pytest.mark.anyio
    async def test_query_warns_for_shadowed_config(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            permission_mode="bypassPermissions",
        )
        with (
            pytest.warns(CanUseToolShadowedWarning, match="bypassPermissions"),
            pytest.raises(_StopBeforeConnectError),
        ):
            async for _ in query(
                prompt=_prompt_stream(), options=options, transport=_failing_transport()
            ):
                pass

    @pytest.mark.anyio
    async def test_query_silent_for_non_shadowed_config(self):
        options = ClaudeAgentOptions(
            can_use_tool=_can_use_tool,
            permission_mode="acceptEdits",
            allowed_tools=["Bash(ls:*)"],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", CanUseToolShadowedWarning)
            with pytest.raises(_StopBeforeConnectError):
                async for _ in query(
                    prompt=_prompt_stream(),
                    options=options,
                    transport=_failing_transport(),
                ):
                    pass
