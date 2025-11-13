"""Tests for Claude SDK type definitions."""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
)
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    StreamEvent,
    SystemMessage,
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


class TestReprStr:
    """Test __repr__ and __str__ methods for all types."""

    def test_text_block_repr_str(self):
        """Test TextBlock repr and str."""
        b = TextBlock(text="Hello, world!")
        assert "TextBlock" in repr(b) and "Hello" in repr(b)
        assert str(b) == "Hello, world!"

    def test_thinking_block_repr_str(self):
        """Test ThinkingBlock repr and str."""
        b = ThinkingBlock(thinking="Let me think...", signature="sig-1")
        assert "ThinkingBlock" in repr(b) and "sig-1" in repr(b)
        assert str(b) == "Let me think..."

    def test_tool_use_block_repr_str(self):
        """Test ToolUseBlock repr and str."""
        b = ToolUseBlock(id="tool-1", name="Read", input={"path": "file.txt"})
        assert "ToolUseBlock" in repr(b) and "Read" in repr(b)
        assert "Tool: Read" in str(b) and "tool-1" in str(b)

    def test_tool_result_block_repr_str(self):
        """Test ToolResultBlock repr and str."""
        b = ToolResultBlock(tool_use_id="tool-1", content="Result", is_error=False)
        assert "ToolResultBlock" in repr(b) and "tool-1" in repr(b)
        assert "success" in str(b) and "tool-1" in str(b)

    def test_user_message_string_repr_str(self):
        """Test UserMessage with string content repr and str."""
        m = UserMessage(content="Hello")
        assert "UserMessage" in repr(m) and "Hello" in repr(m)
        assert str(m) == "Hello"

    def test_user_message_blocks_repr_str(self):
        """Test UserMessage with blocks repr and str."""
        m = UserMessage(
            content=[TextBlock(text="Hi"), ToolUseBlock(id="t1", name="Bash", input={})]
        )
        assert "TextBlock" in repr(m) and "ToolUseBlock" in repr(m)
        assert "UserMessage:" in str(m) and "Bash" in str(m)

    def test_assistant_message_repr_str(self):
        """Test AssistantMessage repr and str."""
        m = AssistantMessage(
            content=[TextBlock(text="Response")], model="claude-sonnet"
        )
        assert "AssistantMessage" in repr(m) and "TextBlock" in repr(m)
        assert "from claude-sonnet" in str(m) and "Response" in str(m)

    def test_system_message_repr_str(self):
        """Test SystemMessage repr and str."""
        m = SystemMessage(subtype="test_type", data={"key": "value"})
        assert "SystemMessage" in repr(m) and "test_type" in repr(m)
        assert "test_type" in str(m)

    def test_result_message_repr_str(self):
        """Test ResultMessage repr and str."""
        m = ResultMessage(
            subtype="ok",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="s1",
        )
        assert "ResultMessage" in repr(m) and "s1" in repr(m)
        assert "success" in str(m) and "1 turn" in str(m)

    def test_stream_event_repr_str(self):
        """Test StreamEvent repr and str."""
        e = StreamEvent(uuid="uuid-123", session_id="s1", event={"type": "message"})
        assert "StreamEvent" in repr(e) and "uuid-123" in repr(e)
        assert "message" in str(e) and "uuid-123" in str(e)

    def test_permission_result_allow_repr_str(self):
        """Test PermissionResultAllow repr and str."""
        p = PermissionResultAllow()
        assert "PermissionResultAllow" in repr(p)
        assert "PermissionResultAllow" in str(p)

    def test_permission_result_deny_repr_str(self):
        """Test PermissionResultDeny repr and str."""
        p = PermissionResultDeny(message="Not allowed")
        assert "PermissionResultDeny" in repr(p) and "Not allowed" in repr(p)
        assert "Not allowed" in str(p)
