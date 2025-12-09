"""Tests for Claude SDK type definitions."""

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SubagentExecutionConfig,
)
from claude_agent_sdk.types import (
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

    def test_claude_code_options_with_subagent_execution(self):
        """Test Options with subagent execution configuration."""
        config = SubagentExecutionConfig(
            multi_invocation="parallel",
            max_concurrent=5,
            error_handling="fail_fast",
        )
        options = ClaudeAgentOptions(subagent_execution=config)
        assert options.subagent_execution is not None
        assert options.subagent_execution.multi_invocation == "parallel"
        assert options.subagent_execution.max_concurrent == 5
        assert options.subagent_execution.error_handling == "fail_fast"


class TestSubagentExecutionConfig:
    """Test SubagentExecutionConfig configuration."""

    def test_default_values(self):
        """Test SubagentExecutionConfig with default values."""
        config = SubagentExecutionConfig()
        assert config.multi_invocation == "sequential"
        assert config.max_concurrent == 3
        assert config.error_handling == "continue"

    def test_parallel_mode(self):
        """Test SubagentExecutionConfig with parallel mode."""
        config = SubagentExecutionConfig(
            multi_invocation="parallel",
            max_concurrent=10,
        )
        assert config.multi_invocation == "parallel"
        assert config.max_concurrent == 10

    def test_error_mode(self):
        """Test SubagentExecutionConfig with error mode for multi-invocation."""
        config = SubagentExecutionConfig(
            multi_invocation="error",
            error_handling="fail_fast",
        )
        assert config.multi_invocation == "error"
        assert config.error_handling == "fail_fast"


class TestAgentDefinition:
    """Test AgentDefinition configuration."""

    def test_basic_agent_definition(self):
        """Test creating a basic AgentDefinition."""
        agent = AgentDefinition(
            description="Test agent",
            prompt="You are a test agent.",
        )
        assert agent.description == "Test agent"
        assert agent.prompt == "You are a test agent."
        assert agent.tools is None
        assert agent.model is None
        assert agent.execution_mode is None

    def test_agent_definition_with_all_fields(self):
        """Test AgentDefinition with all fields specified."""
        agent = AgentDefinition(
            description="Security analyzer",
            prompt="Analyze code for security issues.",
            tools=["Read", "Grep", "Glob"],
            model="haiku",
            execution_mode="parallel",
        )
        assert agent.description == "Security analyzer"
        assert agent.prompt == "Analyze code for security issues."
        assert agent.tools == ["Read", "Grep", "Glob"]
        assert agent.model == "haiku"
        assert agent.execution_mode == "parallel"

    def test_agent_definition_sequential_mode(self):
        """Test AgentDefinition with sequential execution mode."""
        agent = AgentDefinition(
            description="Sequential agent",
            prompt="Run sequentially.",
            execution_mode="sequential",
        )
        assert agent.execution_mode == "sequential"

    def test_agent_definition_auto_mode(self):
        """Test AgentDefinition with auto execution mode."""
        agent = AgentDefinition(
            description="Auto agent",
            prompt="SDK decides execution mode.",
            execution_mode="auto",
        )
        assert agent.execution_mode == "auto"


class TestHookMatcher:
    """Test HookMatcher configuration."""

    def test_default_hook_matcher(self):
        """Test HookMatcher with default values."""
        matcher = HookMatcher()
        assert matcher.matcher is None
        assert matcher.hooks == []
        assert matcher.timeout is None

    def test_hook_matcher_with_simple_tool(self):
        """Test HookMatcher matching a single tool."""
        matcher = HookMatcher(matcher="Bash", timeout=30.0)
        assert matcher.matcher == "Bash"
        assert matcher.timeout == 30.0

    def test_hook_matcher_with_multiple_tools(self):
        """Test HookMatcher matching multiple tools."""
        matcher = HookMatcher(matcher="Write|Edit|MultiEdit")
        assert matcher.matcher == "Write|Edit|MultiEdit"

    def test_hook_matcher_with_mcp_pattern(self):
        """Test HookMatcher with MCP tool pattern."""
        matcher = HookMatcher(matcher="mcp__slack__.*")
        assert matcher.matcher == "mcp__slack__.*"

    def test_hook_matcher_with_mcp_delete_pattern(self):
        """Test HookMatcher matching all MCP delete operations."""
        matcher = HookMatcher(matcher="mcp__.*__delete.*")
        assert matcher.matcher == "mcp__.*__delete.*"

    def test_hook_matcher_with_combined_pattern(self):
        """Test HookMatcher with combined built-in and MCP patterns."""
        matcher = HookMatcher(matcher="Bash|mcp__.*__execute.*")
        assert matcher.matcher == "Bash|mcp__.*__execute.*"
