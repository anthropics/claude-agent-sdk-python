"""Tests for tool permission callbacks and hook callbacks."""

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookContext,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport


class MockTransport(Transport):
    """Mock transport for testing."""

    def __init__(self):
        self.written_messages = []
        self.messages_to_read = []
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def write(self, data: str) -> None:
        self.written_messages.append(data)

    async def end_input(self) -> None:
        pass

    def read_messages(self):
        async def _read():
            for msg in self.messages_to_read:
                yield msg

        return _read()

    def is_ready(self) -> bool:
        return self._connected


class TestToolPermissionCallbacks:
    """Test tool permission callback functionality."""

    @pytest.mark.asyncio
    async def test_permission_callback_allow(self):
        """Test callback that allows tool execution."""
        callback_invoked = False

        async def allow_callback(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultAllow:
            nonlocal callback_invoked
            callback_invoked = True
            assert tool_name == "TestTool"
            assert input_data == {"param": "value"}
            return PermissionResultAllow()

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=allow_callback,
            hooks=None,
        )

        # Simulate control request
        request = {
            "type": "control_request",
            "request_id": "test-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "TestTool",
                "input": {"param": "value"},
                "permission_suggestions": [],
            },
        }

        await query._handle_control_request(request)

        # Check callback was invoked
        assert callback_invoked

        # Check response was sent
        assert len(transport.written_messages) == 1
        response = transport.written_messages[0]
        assert '"behavior": "allow"' in response

    @pytest.mark.asyncio
    async def test_permission_callback_deny(self):
        """Test callback that denies tool execution."""

        async def deny_callback(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultDeny:
            return PermissionResultDeny(message="Security policy violation")

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=deny_callback,
            hooks=None,
        )

        request = {
            "type": "control_request",
            "request_id": "test-2",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "DangerousTool",
                "input": {"command": "rm -rf /"},
                "permission_suggestions": ["deny"],
            },
        }

        await query._handle_control_request(request)

        # Check response
        assert len(transport.written_messages) == 1
        response = transport.written_messages[0]
        assert '"behavior": "deny"' in response
        assert '"message": "Security policy violation"' in response

    @pytest.mark.asyncio
    async def test_permission_callback_input_modification(self):
        """Test callback that modifies tool input."""

        async def modify_callback(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultAllow:
            # Modify the input to add safety flag
            modified_input = input_data.copy()
            modified_input["safe_mode"] = True
            return PermissionResultAllow(updated_input=modified_input)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=modify_callback,
            hooks=None,
        )

        request = {
            "type": "control_request",
            "request_id": "test-3",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "WriteTool",
                "input": {"file_path": "/etc/passwd"},
                "permission_suggestions": [],
            },
        }

        await query._handle_control_request(request)

        # Check response includes modified input
        assert len(transport.written_messages) == 1
        response = transport.written_messages[0]
        assert '"behavior": "allow"' in response
        assert '"safe_mode": true' in response

    @pytest.mark.asyncio
    async def test_callback_exception_handling(self):
        """Test that callback exceptions are properly handled."""

        async def error_callback(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultAllow:
            raise ValueError("Callback error")

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=error_callback,
            hooks=None,
        )

        request = {
            "type": "control_request",
            "request_id": "test-5",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "TestTool",
                "input": {},
                "permission_suggestions": [],
            },
        }

        await query._handle_control_request(request)

        # Check error response was sent
        assert len(transport.written_messages) == 1
        response = transport.written_messages[0]
        assert '"subtype": "error"' in response
        assert "Callback error" in response


class TestHookCallbacks:
    """Test hook callback functionality."""

    @pytest.mark.asyncio
    async def test_hook_execution(self):
        """Test that hooks are called at appropriate times."""
        hook_calls = []

        async def test_hook(
            input_data: dict, tool_use_id: str | None, context: HookContext
        ) -> dict:
            hook_calls.append({"input": input_data, "tool_use_id": tool_use_id})
            return {"processed": True}

        transport = MockTransport()

        # Create hooks configuration
        hooks = {
            "tool_use_start": [{"matcher": {"tool": "TestTool"}, "hooks": [test_hook]}]
        }

        query = Query(
            transport=transport, is_streaming_mode=True, can_use_tool=None, hooks=hooks
        )

        # Manually register the hook callback to avoid needing the full initialize flow
        callback_id = "test_hook_0"
        query.hook_callbacks[callback_id] = test_hook

        # Simulate hook callback request
        request = {
            "type": "control_request",
            "request_id": "test-hook-1",
            "request": {
                "subtype": "hook_callback",
                "callback_id": callback_id,
                "input": {"test": "data"},
                "tool_use_id": "tool-123",
            },
        }

        await query._handle_control_request(request)

        # Check hook was called
        assert len(hook_calls) == 1
        assert hook_calls[0]["input"] == {"test": "data"}
        assert hook_calls[0]["tool_use_id"] == "tool-123"

        # Check response
        assert len(transport.written_messages) > 0
        last_response = transport.written_messages[-1]
        assert '"processed": true' in last_response

    @pytest.mark.asyncio
    async def test_hook_output_fields(self):
        """Test that all SyncHookJSONOutput fields are properly handled."""

        # Test all SyncHookJSONOutput fields together
        async def comprehensive_hook(
            input_data: dict, tool_use_id: str | None, context: HookContext
        ) -> dict:
            return {
                # Control fields
                "continue_": True,
                "suppressOutput": False,
                "stopReason": "Test stop reason",
                # Decision fields
                "decision": "block",
                "systemMessage": "Test system message",
                "reason": "Test reason for blocking",
                # Hook-specific output with all PreToolUse fields
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Security policy violation",
                    "updatedInput": {"modified": "input"},
                },
            }

        transport = MockTransport()
        hooks = {
            "PreToolUse": [
                {"matcher": {"tool": "TestTool"}, "hooks": [comprehensive_hook]}
            ]
        }

        query = Query(
            transport=transport, is_streaming_mode=True, can_use_tool=None, hooks=hooks
        )

        callback_id = "test_comprehensive_hook"
        query.hook_callbacks[callback_id] = comprehensive_hook

        request = {
            "type": "control_request",
            "request_id": "test-comprehensive",
            "request": {
                "subtype": "hook_callback",
                "callback_id": callback_id,
                "input": {"test": "data"},
                "tool_use_id": "tool-456",
            },
        }

        await query._handle_control_request(request)

        # Check response contains all the fields
        assert len(transport.written_messages) > 0
        last_response = transport.written_messages[-1]

        # Verify control fields are present
        assert (
            '"continue_": true' in last_response or '"continue": true' in last_response
        )
        assert (
            '"suppressOutput": false' in last_response
            or '"suppressOutput":false' in last_response
        )
        assert '"stopReason": "Test stop reason"' in last_response

        # Verify decision fields are present
        assert '"decision": "block"' in last_response
        assert '"reason": "Test reason for blocking"' in last_response
        assert '"systemMessage": "Test system message"' in last_response

        # Verify hook-specific output is present
        assert '"hookSpecificOutput"' in last_response
        assert '"permissionDecision": "deny"' in last_response
        assert (
            '"permissionDecisionReason": "Security policy violation"' in last_response
        )
        assert '"updatedInput"' in last_response

    @pytest.mark.asyncio
    async def test_async_hook_output(self):
        """Test AsyncHookJSONOutput type with proper async fields."""

        async def async_hook(
            input_data: dict, tool_use_id: str | None, context: HookContext
        ) -> dict:
            # Test that async hooks properly use async_ and asyncTimeout fields
            return {
                "async_": True,
                "asyncTimeout": 5000,
            }

        transport = MockTransport()
        hooks = {"PreToolUse": [{"matcher": None, "hooks": [async_hook]}]}

        query = Query(
            transport=transport, is_streaming_mode=True, can_use_tool=None, hooks=hooks
        )

        callback_id = "test_async_hook"
        query.hook_callbacks[callback_id] = async_hook

        request = {
            "type": "control_request",
            "request_id": "test-async",
            "request": {
                "subtype": "hook_callback",
                "callback_id": callback_id,
                "input": {"test": "async_data"},
                "tool_use_id": None,
            },
        }

        await query._handle_control_request(request)

        # Check response contains async fields
        assert len(transport.written_messages) > 0
        last_response = transport.written_messages[-1]
        # The SDK should preserve the async_ field (or convert to "async")
        assert '"async_": true' in last_response or '"async": true' in last_response
        assert '"asyncTimeout": 5000' in last_response


class TestClaudeAgentOptionsIntegration:
    """Test that callbacks work through ClaudeAgentOptions."""

    def test_options_with_callbacks(self):
        """Test creating options with callbacks."""

        async def my_callback(
            tool_name: str, input_data: dict, context: ToolPermissionContext
        ) -> PermissionResultAllow:
            return PermissionResultAllow()

        async def my_hook(
            input_data: dict, tool_use_id: str | None, context: HookContext
        ) -> dict:
            return {}

        options = ClaudeAgentOptions(
            can_use_tool=my_callback,
            hooks={
                "tool_use_start": [
                    HookMatcher(matcher={"tool": "Bash"}, hooks=[my_hook])
                ]
            },
        )

        assert options.can_use_tool == my_callback
        assert "tool_use_start" in options.hooks
        assert len(options.hooks["tool_use_start"]) == 1
        assert options.hooks["tool_use_start"][0].hooks[0] == my_hook
