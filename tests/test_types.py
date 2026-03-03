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
