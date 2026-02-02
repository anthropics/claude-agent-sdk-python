"""Tests for Claude SDK type definitions."""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    NotificationHookInput,
    NotificationHookSpecificOutput,
    PermissionRequestHookInput,
    PermissionRequestHookSpecificOutput,
    ResultMessage,
    SubagentStartHookInput,
    SubagentStartHookSpecificOutput,
)
from claude_agent_sdk.types import (
    PostToolUseHookSpecificOutput,
    PreToolUseHookSpecificOutput,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


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
