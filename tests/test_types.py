"""Tests for Claude SDK type definitions."""

from typing import Any, get_args, get_type_hints

from claude_agent_sdk import (
    AssistantMessage,
    BaseHookInput,
    ClaudeAgentOptions,
    EffortLevel,
    NotificationHookInput,
    NotificationHookSpecificOutput,
    PermissionRequestHookInput,
    PermissionRequestHookSpecificOutput,
    PostToolUseFailureHookInput,
    PostToolUseHookInput,
    PreToolUseHookInput,
    ResultMessage,
    StopHookInput,
    SubagentStartHookInput,
    SubagentStartHookSpecificOutput,
    SubagentStopHookInput,
)
from claude_agent_sdk.types import (
    HookSpecificOutput,
    PermissionRuleValue,
    PermissionUpdate,
    PostToolUseHookSpecificOutput,
    PreToolUseHookSpecificOutput,
    SessionStartHookSpecificOutput,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    UserPromptSubmitHookSpecificOutput,
)


def test_effort_level_is_exported():
    """EffortLevel is part of the public package API for downstream wrappers."""
    assert set(get_args(EffortLevel)) == {"low", "medium", "high", "xhigh", "max"}


class TestPermissionUpdate:
    """Test PermissionUpdate wire-format conversion."""

    def test_from_dict_to_dict_roundtrip_add_rules(self):
        wire = {
            "type": "addRules",
            "destination": "localSettings",
            "behavior": "allow",
            "rules": [
                {"toolName": "Bash", "ruleContent": "npm *"},
                {"toolName": "Read", "ruleContent": None},
            ],
        }
        update = PermissionUpdate.from_dict(wire)
        assert update.type == "addRules"
        assert update.destination == "localSettings"
        assert update.behavior == "allow"
        assert update.rules == [
            PermissionRuleValue(tool_name="Bash", rule_content="npm *"),
            PermissionRuleValue(tool_name="Read", rule_content=None),
        ]
        assert update.to_dict() == wire

    def test_from_dict_set_mode(self):
        wire = {"type": "setMode", "mode": "acceptEdits", "destination": "session"}
        update = PermissionUpdate.from_dict(wire)
        assert update.mode == "acceptEdits"
        assert update.rules is None
        assert update.to_dict() == wire

    def test_from_dict_directories(self):
        wire = {
            "type": "addDirectories",
            "directories": ["/tmp/a", "/tmp/b"],
            "destination": "userSettings",
        }
        update = PermissionUpdate.from_dict(wire)
        assert update.directories == ["/tmp/a", "/tmp/b"]
        assert update.to_dict() == wire


class TestMessageTypes:
    """Test message type creation and validation."""

    def test_user_message_creation(self):
        """Test creating a UserMessage."""
        msg = UserMessage(content="Hello, Claude!")
        assert msg.content == "Hello, Claude!"

    def test_assistant_message_with_text(self):
        """Test creating an AssistantMessage with text content."""
        text_block = TextBlock(text="Hello, human!")
        msg = AssistantMessage(content=[text_block], model="claude-opus-4-1-20250805")
        assert len(msg.content) == 1
        assert msg.content[0].text == "Hello, human!"

    def test_assistant_message_with_thinking(self):
        """Test creating an AssistantMessage with thinking content."""
        thinking_block = ThinkingBlock(thinking="I'm thinking...", signature="sig-123")
        msg = AssistantMessage(
            content=[thinking_block], model="claude-opus-4-1-20250805"
        )
        assert len(msg.content) == 1
        assert msg.content[0].thinking == "I'm thinking..."
        assert msg.content[0].signature == "sig-123"

    def test_tool_use_block(self):
        """Test creating a ToolUseBlock."""
        block = ToolUseBlock(
            id="tool-123", name="Read", input={"file_path": "/test.txt"}
        )
        assert block.id == "tool-123"
        assert block.name == "Read"
        assert block.input["file_path"] == "/test.txt"

    def test_tool_result_block(self):
        """Test creating a ToolResultBlock."""
        block = ToolResultBlock(
            tool_use_id="tool-123", content="File contents here", is_error=False
        )
        assert block.tool_use_id == "tool-123"
        assert block.content == "File contents here"
        assert block.is_error is False

    def test_result_message(self):
        """Test creating a ResultMessage."""
        msg = ResultMessage(
            subtype="success",
            duration_ms=1500,
            duration_api_ms=1200,
            is_error=False,
            num_turns=1,
            session_id="session-123",
            total_cost_usd=0.01,
        )
        assert msg.subtype == "success"
        assert msg.total_cost_usd == 0.01
        assert msg.session_id == "session-123"


class TestOptions:
    """Test Options configuration."""

    def test_default_options(self):
        """Test Options with default values."""
        options = ClaudeAgentOptions()
        assert options.allowed_tools == []
        assert options.system_prompt is None
        assert options.permission_mode is None
        assert options.continue_conversation is False
        assert options.disallowed_tools == []

    def test_claude_code_options_with_tools(self):
        """Test Options with built-in tools."""
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Edit"], disallowed_tools=["Bash"]
        )
        assert options.allowed_tools == ["Read", "Write", "Edit"]
        assert options.disallowed_tools == ["Bash"]

    def test_claude_code_options_with_permission_mode(self):
        """Test Options with permission mode."""
        options = ClaudeAgentOptions(permission_mode="bypassPermissions")
        assert options.permission_mode == "bypassPermissions"

        options_plan = ClaudeAgentOptions(permission_mode="plan")
        assert options_plan.permission_mode == "plan"

        options_default = ClaudeAgentOptions(permission_mode="default")
        assert options_default.permission_mode == "default"

        options_accept = ClaudeAgentOptions(permission_mode="acceptEdits")
        assert options_accept.permission_mode == "acceptEdits"

        options_dont_ask = ClaudeAgentOptions(permission_mode="dontAsk")
        assert options_dont_ask.permission_mode == "dontAsk"

        options_auto = ClaudeAgentOptions(permission_mode="auto")
        assert options_auto.permission_mode == "auto"

    def test_claude_code_options_with_system_prompt_string(self):
        """Test Options with system prompt as string."""
        options = ClaudeAgentOptions(
            system_prompt="You are a helpful assistant.",
        )
        assert options.system_prompt == "You are a helpful assistant."

    def test_claude_code_options_with_system_prompt_preset(self):
        """Test Options with system prompt preset."""
        options = ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code"},
        )
        assert options.system_prompt == {"type": "preset", "preset": "claude_code"}

    def test_claude_code_options_with_system_prompt_preset_and_append(self):
        """Test Options with system prompt preset and append."""
        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": "Be concise.",
            },
        )
        assert options.system_prompt == {
            "type": "preset",
            "preset": "claude_code",
            "append": "Be concise.",
        }

    def test_claude_code_options_with_system_prompt_preset_exclude_dynamic_sections(
        self,
    ):
        """Test Options with system prompt preset and exclude_dynamic_sections."""
        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "exclude_dynamic_sections": True,
            },
        )
        assert options.system_prompt == {
            "type": "preset",
            "preset": "claude_code",
            "exclude_dynamic_sections": True,
        }

    def test_claude_code_options_with_system_prompt_preset_snapshot(self):
        """Test Options with system prompt preset and snapshot."""
        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": "Be concise.",
                "snapshot": False,
            },
        )
        assert options.system_prompt == {
            "type": "preset",
            "preset": "claude_code",
            "append": "Be concise.",
            "snapshot": False,
        }

    def test_claude_code_options_with_system_prompt_custom(self):
        """Test Options with the custom system prompt form."""
        options = ClaudeAgentOptions(
            system_prompt={
                "type": "custom",
                "prompt": "You are a release bot.",
                "snapshot": True,
            },
        )
        assert options.system_prompt == {
            "type": "custom",
            "prompt": "You are a release bot.",
            "snapshot": True,
        }

    def test_claude_code_options_with_system_prompt_file(self):
        """Test Options with system prompt file."""
        options = ClaudeAgentOptions(
            system_prompt={"type": "file", "path": "/path/to/prompt.md"},
        )
        assert options.system_prompt == {
            "type": "file",
            "path": "/path/to/prompt.md",
        }

    def test_claude_code_options_with_session_continuation(self):
        """Test Options with session continuation."""
        options = ClaudeAgentOptions(continue_conversation=True, resume="session-123")
        assert options.continue_conversation is True
        assert options.resume == "session-123"

    def test_claude_code_options_with_model_specification(self):
        """Test Options with model specification."""
        options = ClaudeAgentOptions(
            model="claude-sonnet-4-5", permission_prompt_tool_name="CustomTool"
        )
        assert options.model == "claude-sonnet-4-5"
        assert options.permission_prompt_tool_name == "CustomTool"


class TestHookInputTypes:
    """Test hook input type definitions."""

    def test_notification_hook_input(self):
        """Test NotificationHookInput construction."""
        hook_input: NotificationHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "Notification",
            "message": "Task completed",
            "notification_type": "info",
        }
        assert hook_input["hook_event_name"] == "Notification"
        assert hook_input["message"] == "Task completed"
        assert hook_input["notification_type"] == "info"

    def test_notification_hook_input_with_title(self):
        """Test NotificationHookInput with optional title."""
        hook_input: NotificationHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "Notification",
            "message": "Task completed",
            "notification_type": "info",
            "title": "Success",
        }
        assert hook_input["title"] == "Success"

    def test_subagent_start_hook_input(self):
        """Test SubagentStartHookInput construction."""
        hook_input: SubagentStartHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-42",
            "agent_type": "researcher",
        }
        assert hook_input["hook_event_name"] == "SubagentStart"
        assert hook_input["agent_id"] == "agent-42"
        assert hook_input["agent_type"] == "researcher"

    def test_pre_tool_use_hook_input_with_agent_id(self):
        """PreToolUseHookInput accepts optional agent_id/agent_type.

        When a tool is called from inside a Task sub-agent, the CLI includes
        the calling agent's id so consumers can correlate the tool call to
        the correct sub-agent — parallel sub-agents interleave their hook
        callbacks over the same control channel and are otherwise
        indistinguishable.
        """
        from claude_agent_sdk.types import PreToolUseHookInput

        # Tool called from inside a sub-agent: agent_id present,
        # same value SubagentStart emits.
        hook_input: PreToolUseHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_use_id": "toolu_abc123",
            "agent_id": "agent-42",
            "agent_type": "researcher",
        }
        assert hook_input.get("agent_id") == "agent-42"
        assert hook_input.get("agent_type") == "researcher"

        # Tool called on the main thread: agent_id absent. Still type-valid.
        hook_input_main: PreToolUseHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_use_id": "toolu_def456",
        }
        assert hook_input_main.get("agent_id") is None

    def test_post_tool_use_hook_input_with_agent_id(self):
        """PostToolUseHookInput accepts optional agent_id."""
        from claude_agent_sdk.types import PostToolUseHookInput

        hook_input: PostToolUseHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_response": {"content": [{"type": "text", "text": "hello"}]},
            "tool_use_id": "toolu_abc123",
            "agent_id": "agent-42",
        }
        assert hook_input.get("agent_id") == "agent-42"

    def test_permission_request_hook_input(self):
        """Test PermissionRequestHookInput construction."""
        hook_input: PermissionRequestHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        assert hook_input["hook_event_name"] == "PermissionRequest"
        assert hook_input["tool_name"] == "Bash"
        assert hook_input["tool_input"] == {"command": "ls"}

    def test_permission_request_hook_input_with_suggestions(self):
        """Test PermissionRequestHookInput with optional permission_suggestions."""
        hook_input: PermissionRequestHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "permission_suggestions": [{"type": "allow", "rule": "Bash(*)"}],
        }
        assert len(hook_input["permission_suggestions"]) == 1


class TestHookSpecificOutputTypes:
    """Test hook-specific output type definitions."""

    def test_notification_hook_specific_output(self):
        """Test NotificationHookSpecificOutput construction."""
        output: NotificationHookSpecificOutput = {
            "hookEventName": "Notification",
            "additionalContext": "Extra info",
        }
        assert output["hookEventName"] == "Notification"
        assert output["additionalContext"] == "Extra info"

    def test_subagent_start_hook_specific_output(self):
        """Test SubagentStartHookSpecificOutput construction."""
        output: SubagentStartHookSpecificOutput = {
            "hookEventName": "SubagentStart",
            "additionalContext": "Starting subagent for research",
        }
        assert output["hookEventName"] == "SubagentStart"

    def test_permission_request_hook_specific_output(self):
        """Test PermissionRequestHookSpecificOutput construction."""
        output: PermissionRequestHookSpecificOutput = {
            "hookEventName": "PermissionRequest",
            "decision": {"type": "allow"},
        }
        assert output["hookEventName"] == "PermissionRequest"
        assert output["decision"] == {"type": "allow"}

    def test_pre_tool_use_output_has_additional_context(self):
        """Test PreToolUseHookSpecificOutput includes additionalContext field."""
        output: PreToolUseHookSpecificOutput = {
            "hookEventName": "PreToolUse",
            "additionalContext": "context for claude",
        }
        assert output["additionalContext"] == "context for claude"

    def test_post_tool_use_output_has_updated_mcp_tool_output(self):
        """Test PostToolUseHookSpecificOutput includes updatedMCPToolOutput field."""
        output: PostToolUseHookSpecificOutput = {
            "hookEventName": "PostToolUse",
            "updatedMCPToolOutput": {"result": "modified"},
        }
        assert output["updatedMCPToolOutput"] == {"result": "modified"}

    def test_post_tool_use_output_has_updated_tool_output(self):
        """Test PostToolUseHookSpecificOutput includes updatedToolOutput field."""
        output: PostToolUseHookSpecificOutput = {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": {
                "stdout": "replaced",
                "stderr": "",
                "interrupted": False,
            },
        }
        assert output["updatedToolOutput"] == {
            "stdout": "replaced",
            "stderr": "",
            "interrupted": False,
        }

    def test_user_prompt_submit_output_session_title_and_suppress(self):
        output: UserPromptSubmitHookSpecificOutput = {
            "hookEventName": "UserPromptSubmit",
            "sessionTitle": "Refactor auth",
            "suppressOriginalPrompt": True,
        }
        assert output["sessionTitle"] == "Refactor auth"
        assert output["suppressOriginalPrompt"] is True

    def test_session_start_output_fields(self):
        output: SessionStartHookSpecificOutput = {
            "hookEventName": "SessionStart",
            "additionalContext": "ctx",
            "initialUserMessage": "Run the test suite",
            "sessionTitle": "auth-refactor",
            "watchPaths": ["/repo/.envrc"],
            "reloadSkills": True,
        }
        assert output["watchPaths"] == ["/repo/.envrc"]
        assert output["reloadSkills"] is True

    # (required keys, optional keys) for each hook-specific output, matching
    # the TypeScript SDK's *HookSpecificOutput types. Checked against the
    # TypedDict key sets because mypy does not run over tests/.
    _OUTPUT_KEYS: dict[str, tuple[set[str], set[str]]] = {
        "UserPromptSubmitHookSpecificOutput": (
            {"hookEventName"},
            {"additionalContext", "sessionTitle", "suppressOriginalPrompt"},
        ),
        "UserPromptExpansionHookSpecificOutput": (
            {"hookEventName"},
            {"additionalContext", "suppressOriginalPrompt"},
        ),
        "SessionStartHookSpecificOutput": (
            {"hookEventName"},
            {
                "additionalContext",
                "initialUserMessage",
                "sessionTitle",
                "watchPaths",
                "reloadSkills",
            },
        ),
        "PostToolBatchHookSpecificOutput": ({"hookEventName"}, {"additionalContext"}),
        "StopHookSpecificOutput": ({"hookEventName"}, {"additionalContext"}),
        "SubagentStopHookSpecificOutput": ({"hookEventName"}, {"additionalContext"}),
        "PermissionDeniedHookSpecificOutput": ({"hookEventName"}, {"retry"}),
        "PreModelSwitchHookSpecificOutput": (
            {"hookEventName"},
            {"permissionDecision", "permissionDecisionReason"},
        ),
        "PostModelSwitchHookSpecificOutput": (
            {"hookEventName"},
            {"additionalContext"},
        ),
        "ElicitationHookSpecificOutput": ({"hookEventName"}, {"action", "content"}),
        "ElicitationResultHookSpecificOutput": (
            {"hookEventName"},
            {"action", "content"},
        ),
        "CwdChangedHookSpecificOutput": ({"hookEventName"}, {"watchPaths"}),
        "FileChangedHookSpecificOutput": ({"hookEventName"}, {"watchPaths"}),
        "WorktreeCreateHookSpecificOutput": ({"hookEventName", "worktreePath"}, set()),
        "MessageDisplayHookSpecificOutput": ({"hookEventName"}, {"displayContent"}),
    }

    def test_output_key_sets_match_typescript(self):
        import claude_agent_sdk

        for name, (required, optional) in self._OUTPUT_KEYS.items():
            typed_dict = getattr(claude_agent_sdk, name)
            assert typed_dict.__required_keys__ == required, name
            assert typed_dict.__optional_keys__ == optional, name
            assert typed_dict in get_args(HookSpecificOutput), name

    def test_output_literals(self):
        import claude_agent_sdk

        def literal_values(tp: Any) -> set[Any]:
            # Unwrap NotRequired[...], which get_type_hints keeps on 3.10.
            args = get_args(tp)
            while len(args) == 1 and get_args(args[0]):
                args = get_args(args[0])
            return set(args)

        pre_model_switch = get_type_hints(
            claude_agent_sdk.PreModelSwitchHookSpecificOutput
        )
        assert literal_values(pre_model_switch["permissionDecision"]) == {
            "allow",
            "deny",
            "ask",
        }
        for name in (
            "ElicitationHookSpecificOutput",
            "ElicitationResultHookSpecificOutput",
        ):
            hints = get_type_hints(getattr(claude_agent_sdk, name))
            assert literal_values(hints["action"]) == {"accept", "decline", "cancel"}

    def test_hook_specific_output_members_have_distinct_event_names(self):
        """Each HookSpecificOutput member is discriminated by its own event."""
        names = []
        for member in get_args(HookSpecificOutput):
            (event_name,) = get_args(get_type_hints(member)["hookEventName"])
            names.append(event_name)
        assert len(names) == len(set(names))

    def test_every_hook_specific_output_is_exported(self):
        import claude_agent_sdk

        for member in get_args(HookSpecificOutput):
            assert member.__name__ in claude_agent_sdk.__all__, member.__name__
            assert getattr(claude_agent_sdk, member.__name__) is member


class TestHookInputFieldParity:
    """Optional fields the CLI sends on existing hook inputs."""

    def test_base_hook_input_prompt_id_and_effort(self):
        hook_input: PreToolUseHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
            "effort": {"level": "high"},
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_use_id": "toolu_1",
        }
        assert hook_input.get("prompt_id") == "550e8400-e29b-41d4-a716-446655440000"
        assert hook_input["effort"]["level"] == "high"

    def test_base_hook_input_new_fields_are_optional(self):
        assert "prompt_id" in BaseHookInput.__optional_keys__
        assert "effort" in BaseHookInput.__optional_keys__

    def test_post_tool_use_duration_ms(self):
        hook_input: PostToolUseHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"stdout": ""},
            "tool_use_id": "toolu_1",
            "duration_ms": 12,
        }
        assert hook_input["duration_ms"] == 12
        assert "duration_ms" in PostToolUseFailureHookInput.__optional_keys__

    def test_stop_hook_input_background_work(self):
        hook_input: StopHookInput = {
            "session_id": "sess-1",
            "transcript_path": "/tmp/transcript",
            "cwd": "/home/user",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "Done.",
            "background_tasks": [
                {
                    "id": "task-1",
                    "type": "shell",
                    "status": "running",
                    "description": "npm test",
                    "command": "npm test",
                }
            ],
            "session_crons": [
                {
                    "id": "cron-1",
                    "schedule": "0 9 * * 1-5",
                    "recurring": True,
                    "prompt": "check CI",
                }
            ],
        }
        assert hook_input["background_tasks"][0]["command"] == "npm test"
        assert hook_input["session_crons"][0]["recurring"] is True

    def test_subagent_stop_hook_input_fields_are_optional(self):
        for key in ("last_assistant_message", "background_tasks", "session_crons"):
            assert key in SubagentStopHookInput.__optional_keys__
            assert key in StopHookInput.__optional_keys__

    def test_background_work_summary_key_sets(self):
        import claude_agent_sdk

        tasks = claude_agent_sdk.BackgroundTaskSummary
        assert tasks.__required_keys__ == {"id", "type", "status", "description"}
        assert tasks.__optional_keys__ == {
            "command",
            "agent_type",
            "server",
            "tool",
            "name",
        }
        crons = claude_agent_sdk.SessionCronSummary
        assert crons.__required_keys__ == {"id", "schedule", "recurring", "prompt"}
        assert crons.__optional_keys__ == set()
        assert claude_agent_sdk.HookEffort.__required_keys__ == {"level"}


class TestSystemInitData:
    """SystemInitData describes SystemMessage.data for init frames."""

    def test_key_sets_match_typescript(self):
        """Keys match the documented fields of the TS SDK's SDKSystemMessage."""
        from claude_agent_sdk import SystemInitData

        assert SystemInitData.__required_keys__ == {
            "type",
            "subtype",
            "uuid",
            "session_id",
            "apiKeySource",
            "claude_code_version",
            "cwd",
            "tools",
            "mcp_servers",
            "model",
            "permissionMode",
            "slash_commands",
            "output_style",
            "skills",
            "plugins",
        }
        assert SystemInitData.__optional_keys__ == {
            "agents",
            "betas",
            "terminal_slash_commands",
            "plugin_errors",
            "fast_mode_state",
            "fast_mode_disabled_reason",
            "capabilities",
        }

    def test_nested_key_sets(self):
        from claude_agent_sdk import (
            SystemInitMcpServer,
            SystemInitPlugin,
            SystemInitPluginError,
        )

        assert SystemInitMcpServer.__required_keys__ == {"name", "status"}
        assert SystemInitMcpServer.__optional_keys__ == {"source"}
        assert SystemInitPlugin.__required_keys__ == {"name", "path"}
        assert SystemInitPlugin.__optional_keys__ == {"version"}
        assert SystemInitPluginError.__required_keys__ == {
            "plugin",
            "type",
            "message",
        }
        assert SystemInitPluginError.__optional_keys__ == {"path"}

    def test_fast_mode_literals(self):
        from claude_agent_sdk import FastModeDisabledReason, FastModeState

        assert set(get_args(FastModeState)) == {"off", "cooldown", "on"}
        assert "sdk_opt_in_required" in get_args(FastModeDisabledReason)

    def test_init_frame_still_parses_to_plain_system_message(self):
        """Typing the payload does not change what the parser returns."""
        from typing import cast

        from claude_agent_sdk import SystemInitData, SystemMessage
        from claude_agent_sdk._internal.message_parser import parse_message

        data = {
            "type": "system",
            "subtype": "init",
            "uuid": "u1",
            "session_id": "s1",
            "apiKeySource": "none",
            "claude_code_version": "2.1.284",
            "cwd": "/repo",
            "tools": ["Bash"],
            "mcp_servers": [{"name": "docs", "status": "connected"}],
            "model": "claude-sonnet-5",
            "permissionMode": "default",
            "slash_commands": ["compact"],
            "output_style": "default",
            "skills": [],
            "plugins": [],
        }
        message = parse_message(data)
        assert type(message) is SystemMessage
        assert message.data == data
        init = cast(SystemInitData, message.data)
        assert init["mcp_servers"][0]["status"] == "connected"
        assert init.get("capabilities") is None


class TestMcpServerStatusTypes:
    """Test MCP server status type definitions."""

    def test_mcp_server_status_importable_from_package(self):
        """Verify McpServerStatus and related types are exported."""
        from claude_agent_sdk import (
            McpServerConnectionStatus,  # noqa: F401
            McpServerInfo,  # noqa: F401
            McpServerStatus,  # noqa: F401
            McpServerStatusConfig,  # noqa: F401
            McpStatusResponse,  # noqa: F401
            McpToolAnnotations,  # noqa: F401
            McpToolInfo,  # noqa: F401
        )

    def test_mcp_server_status_connected(self):
        """Test constructing a connected McpServerStatus with full fields."""
        from claude_agent_sdk import McpServerStatus

        status: McpServerStatus = {
            "name": "my-server",
            "status": "connected",
            "serverInfo": {"name": "my-server", "version": "1.2.3"},
            "config": {"type": "http", "url": "https://example.com"},
            "scope": "project",
            "tools": [
                {
                    "name": "greet",
                    "description": "Greet a user",
                    "annotations": {
                        "readOnly": True,
                        "destructive": False,
                        "openWorld": False,
                    },
                }
            ],
        }
        assert status["name"] == "my-server"
        assert status["status"] == "connected"
        assert status["serverInfo"]["version"] == "1.2.3"
        assert status["tools"][0]["annotations"]["readOnly"] is True

    def test_mcp_server_status_minimal(self):
        """Test constructing a minimal McpServerStatus (only required fields)."""
        from claude_agent_sdk import McpServerStatus

        status: McpServerStatus = {"name": "pending-server", "status": "pending"}
        assert status["name"] == "pending-server"
        assert status["status"] == "pending"
        assert "error" not in status
        assert "config" not in status

    def test_mcp_server_status_failed_with_error(self):
        """Test McpServerStatus for a failed server includes error."""
        from claude_agent_sdk import McpServerStatus

        status: McpServerStatus = {
            "name": "broken-server",
            "status": "failed",
            "error": "Connection refused",
        }
        assert status["status"] == "failed"
        assert status["error"] == "Connection refused"

    def test_mcp_server_status_config_claudeai_proxy(self):
        """Test McpServerStatusConfig accepts claudeai-proxy variant."""
        from claude_agent_sdk import McpServerStatus

        status: McpServerStatus = {
            "name": "proxy-server",
            "status": "needs-auth",
            "config": {
                "type": "claudeai-proxy",
                "url": "https://claude.ai/proxy",
                "id": "proxy-abc",
            },
        }
        assert status["config"]["type"] == "claudeai-proxy"
        assert status["config"]["id"] == "proxy-abc"

    def test_mcp_status_response_wraps_servers(self):
        """Test McpStatusResponse wraps mcpServers list."""
        from claude_agent_sdk import McpStatusResponse

        response: McpStatusResponse = {
            "mcpServers": [
                {"name": "a", "status": "connected"},
                {"name": "b", "status": "disabled"},
            ]
        }
        assert len(response["mcpServers"]) == 2
        assert response["mcpServers"][0]["status"] == "connected"
        assert response["mcpServers"][1]["status"] == "disabled"


class TestAgentDefinition:
    """Test AgentDefinition serialization contract.

    AgentDefinition is sent to the CLI via the initialize control request.
    The _internal/client.py serializer uses ``asdict()`` directly, so field
    names here must match the CLI's expected JSON keys exactly.
    """

    def _serialize(self, agent):
        # Mirror the transform in _internal/client.py and client.py:
        #   {k: v for k, v in asdict(agent_def).items() if v is not None}
        from dataclasses import asdict

        return {k: v for k, v in asdict(agent).items() if v is not None}

    def test_minimal_definition_omits_unset_fields(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(description="test", prompt="You are a test")
        payload = self._serialize(agent)

        assert payload == {"description": "test", "prompt": "You are a test"}

    def test_skills_and_memory_serialize_with_cli_keys(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            skills=["skill-a", "skill-b"],
            memory="project",
        )
        payload = self._serialize(agent)

        assert payload["skills"] == ["skill-a", "skill-b"]
        assert payload["memory"] == "project"

    def test_mcp_servers_serializes_as_camelcase(self):
        """CLI expects ``mcpServers`` (camelCase), not snake_case."""
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            mcpServers=[
                "slack",
                {"local": {"command": "python", "args": ["server.py"]}},
            ],
        )
        payload = self._serialize(agent)

        assert "mcpServers" in payload
        assert "mcp_servers" not in payload
        assert payload["mcpServers"][0] == "slack"
        assert payload["mcpServers"][1]["local"]["command"] == "python"

    def test_disallowed_tools_and_max_turns_serialize_as_camelcase(self):
        """CLI expects ``disallowedTools`` and ``maxTurns`` (camelCase)."""
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            disallowedTools=["Bash", "Write"],
            maxTurns=10,
        )
        payload = self._serialize(agent)

        assert payload["disallowedTools"] == ["Bash", "Write"]
        assert "disallowed_tools" not in payload
        assert payload["maxTurns"] == 10
        assert "max_turns" not in payload

    def test_initial_prompt_serializes_as_camelcase(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            initialPrompt="/review-pr 123",
        )
        payload = self._serialize(agent)

        assert payload["initialPrompt"] == "/review-pr 123"
        assert "initial_prompt" not in payload

    def test_model_accepts_full_model_id(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            model="claude-opus-4-5",
        )
        payload = self._serialize(agent)

        assert payload["model"] == "claude-opus-4-5"

    def test_background_serializes_correctly(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            background=True,
        )
        payload = self._serialize(agent)

        assert payload["background"] is True

    def test_effort_accepts_named_level(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            effort="high",
        )
        payload = self._serialize(agent)

        assert payload["effort"] == "high"

    def test_effort_accepts_xhigh_level(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            effort="xhigh",
        )
        payload = self._serialize(agent)

        assert payload["effort"] == "xhigh"

    def test_effort_accepts_integer(self):
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            effort=32000,
        )
        payload = self._serialize(agent)

        assert payload["effort"] == 32000

    def test_permission_mode_serializes_as_camelcase(self):
        """CLI expects ``permissionMode`` (camelCase)."""
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(
            description="test",
            prompt="p",
            permissionMode="bypassPermissions",
        )
        payload = self._serialize(agent)

        assert payload["permissionMode"] == "bypassPermissions"
        assert "permission_mode" not in payload

    def test_new_fields_omitted_when_none(self):
        """New optional fields should not appear in payload when unset."""
        from claude_agent_sdk import AgentDefinition

        agent = AgentDefinition(description="test", prompt="p")
        payload = self._serialize(agent)

        assert "background" not in payload
        assert "effort" not in payload
        assert "permissionMode" not in payload
