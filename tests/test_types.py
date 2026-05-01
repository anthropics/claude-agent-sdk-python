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
    MirrorErrorMessage,
    PostToolUseHookSpecificOutput,
    PreToolUseHookSpecificOutput,
    RateLimitEvent,
    RateLimitInfo,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUsage,
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


class TestRepr:
    """Tests for __repr__ methods on all 17 repr-carrying types."""

    def test_text_block_repr(self) -> None:
        """TextBlock repr includes class name and truncated text field."""
        obj = TextBlock(text="Hello, world!")
        r = repr(obj)
        assert r.startswith("TextBlock(")
        assert "Hello, world!" in r

    def test_thinking_block_repr(self) -> None:
        """ThinkingBlock repr includes class name and truncated thinking field."""
        obj = ThinkingBlock(
            thinking="I am reasoning step by step.", signature="sig-abc"
        )
        r = repr(obj)
        assert r.startswith("ThinkingBlock(")
        assert "I am reasoning step by step." in r

    def test_tool_use_block_repr(self) -> None:
        """ToolUseBlock repr includes id, name, and input."""
        obj = ToolUseBlock(
            id="toolu_abc123", name="Read", input={"file_path": "/src/main.py"}
        )
        r = repr(obj)
        assert r.startswith("ToolUseBlock(")
        assert "toolu_abc123" in r
        assert "Read" in r
        assert "input=" in r

    def test_tool_result_block_repr(self) -> None:
        """ToolResultBlock repr includes tool_use_id and is_error."""
        obj = ToolResultBlock(
            tool_use_id="toolu_abc123", content="file contents", is_error=False
        )
        r = repr(obj)
        assert r.startswith("ToolResultBlock(")
        assert "toolu_abc123" in r
        assert "False" in r

    def test_server_tool_use_block_repr(self) -> None:
        """ServerToolUseBlock repr includes id, name, and input."""
        obj = ServerToolUseBlock(
            id="srv-001", name="web_search", input={"query": "python repr"}
        )
        r = repr(obj)
        assert r.startswith("ServerToolUseBlock(")
        assert "srv-001" in r
        assert "web_search" in r

    def test_server_tool_result_block_repr(self) -> None:
        """ServerToolResultBlock repr includes tool_use_id and content."""
        obj = ServerToolResultBlock(
            tool_use_id="srv-001", content={"type": "text", "text": "results here"}
        )
        r = repr(obj)
        assert r.startswith("ServerToolResultBlock(")
        assert "srv-001" in r
        assert "content=" in r

    def test_user_message_repr_str_branch(self) -> None:
        """UserMessage repr (str content) includes truncated content and uuid."""
        obj = UserMessage(content="Hello, Claude!", uuid="uuid-001")
        r = repr(obj)
        assert r.startswith("UserMessage(")
        assert "Hello, Claude!" in r
        assert "uuid-001" in r

    def test_user_message_repr_list_branch(self) -> None:
        """UserMessage repr (list content) shows item count instead of raw content."""
        blocks = [TextBlock(text="hi"), TextBlock(text="there")]
        obj = UserMessage(content=blocks, uuid="uuid-002")
        r = repr(obj)
        assert r.startswith("UserMessage(")
        assert "[2 items]" in r
        assert "uuid-002" in r

    def test_assistant_message_repr(self) -> None:
        """AssistantMessage repr includes model, stop_reason, and content count."""
        obj = AssistantMessage(
            content=[TextBlock(text="Here is my answer.")],
            model="claude-opus-4-5",
            stop_reason="end_turn",
        )
        r = repr(obj)
        assert r.startswith("AssistantMessage(")
        assert "claude-opus-4-5" in r
        assert "end_turn" in r
        assert "[1 items]" in r

    def test_system_message_repr(self) -> None:
        """SystemMessage repr includes subtype and truncated data."""
        obj = SystemMessage(
            subtype="init", data={"session": "sess-1", "model": "claude"}
        )
        r = repr(obj)
        assert r.startswith("SystemMessage(")
        assert "init" in r
        assert "data=" in r

    def test_result_message_repr(self) -> None:
        """ResultMessage repr includes subtype, is_error, duration_ms, session_id."""
        obj = ResultMessage(
            subtype="success",
            duration_ms=1500,
            duration_api_ms=1200,
            is_error=False,
            num_turns=3,
            session_id="sess-xyz",
        )
        r = repr(obj)
        assert r.startswith("ResultMessage(")
        assert "success" in r
        assert "False" in r
        assert "1500" in r
        assert "sess-xyz" in r

    def test_task_started_message_repr(self) -> None:
        """TaskStartedMessage repr includes subtype and session_id."""
        obj = TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="task-001",
            description="Run linter",
            uuid="uuid-ts",
            session_id="sess-ts",
        )
        r = repr(obj)
        assert r.startswith("TaskStartedMessage(")
        assert "task_started" in r
        assert "sess-ts" in r

    def test_task_progress_message_repr(self) -> None:
        """TaskProgressMessage repr includes subtype and description."""
        usage: TaskUsage = {"total_tokens": 500, "tool_uses": 2, "duration_ms": 800}
        obj = TaskProgressMessage(
            subtype="task_progress",
            data={},
            task_id="task-001",
            description="Running tests now",
            usage=usage,
            uuid="uuid-tp",
            session_id="sess-tp",
        )
        r = repr(obj)
        assert r.startswith("TaskProgressMessage(")
        assert "task_progress" in r
        assert "Running tests now" in r

    def test_task_notification_message_repr(self) -> None:
        """TaskNotificationMessage repr includes subtype and status."""
        obj = TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="task-001",
            status="completed",
            output_file="/tmp/output.txt",
            summary="All done",
            uuid="uuid-tn",
            session_id="sess-tn",
        )
        r = repr(obj)
        assert r.startswith("TaskNotificationMessage(")
        assert "task_notification" in r
        assert "completed" in r

    def test_mirror_error_message_repr(self) -> None:
        """MirrorErrorMessage repr includes subtype and error."""
        obj = MirrorErrorMessage(
            subtype="mirror_error",
            data={},
            error="DB write timed out",
        )
        r = repr(obj)
        assert r.startswith("MirrorErrorMessage(")
        assert "mirror_error" in r
        assert "DB write timed out" in r

    def test_stream_event_repr(self) -> None:
        """StreamEvent repr includes event_type and session_id."""
        obj = StreamEvent(
            uuid="uuid-se",
            session_id="sess-se",
            event={"type": "content_block_delta", "index": 0},
        )
        r = repr(obj)
        assert r.startswith("StreamEvent(")
        assert "content_block_delta" in r
        assert "sess-se" in r

    def test_rate_limit_info_repr(self) -> None:
        """RateLimitInfo repr includes status and raw."""
        obj = RateLimitInfo(status="allowed_warning", raw={"utilization": 0.85})
        r = repr(obj)
        assert r.startswith("RateLimitInfo(")
        assert "allowed_warning" in r
        assert "raw=" in r

    def test_rate_limit_event_repr(self) -> None:
        """RateLimitEvent repr embeds the RateLimitInfo repr."""
        info = RateLimitInfo(status="rejected", raw={})
        obj = RateLimitEvent(
            rate_limit_info=info, uuid="uuid-rle", session_id="sess-rle"
        )
        r = repr(obj)
        assert r.startswith("RateLimitEvent(")
        assert "RateLimitInfo(" in r
        assert "rejected" in r

    def test_tool_use_block_repr_truncation(self) -> None:
        """ToolUseBlock repr truncates large input dicts with '...'."""
        obj = ToolUseBlock(id="x", name="fn", input={"k": "a" * 200})
        r = repr(obj)
        assert "ToolUseBlock" in repr(obj)
        assert "..." in r

    def test_tool_result_block_repr_truncation(self) -> None:
        """ToolResultBlock repr truncates large content with '...'."""
        obj = ToolResultBlock(tool_use_id="x", content=["a" * 200], is_error=False)
        r = repr(obj)
        assert "ToolResultBlock" in repr(obj)
        assert "..." in r

    def test_repr_none_fields(self) -> None:
        """ToolResultBlock repr renders None fields as 'None'."""
        obj = ToolResultBlock(tool_use_id="x", content=None, is_error=None)
        r = repr(obj)
        assert "ToolResultBlock" in r
        assert "None" in r

    def test_repr_empty_list(self) -> None:
        """UserMessage repr with empty list content shows [0 items]."""
        obj = UserMessage(content=[])
        r = repr(obj)
        assert "UserMessage" in r
        assert "[0 items]" in r

    def test_repr_unicode(self) -> None:
        """TextBlock repr preserves unicode content correctly."""
        obj = TextBlock(text="こんにちは世界")
        r = repr(obj)
        assert "TextBlock" in r
        assert "こんにちは世界" in r
