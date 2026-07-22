"""Tests for governance_hook — policy-as-code layer for tool call authorization."""

import json

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    GovernanceDecision,
    GovernanceHook,
    PermissionResultAllow,
    ToolPermissionContext,
)
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport import Transport

# ---------------------------------------------------------------------------
# Minimal mock transport (same pattern as test_tool_callbacks.py)
# ---------------------------------------------------------------------------


class MockTransport(Transport):
    def __init__(self):
        self.written_messages: list[str] = []
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
            return
            yield  # make it an async generator

        return _read()

    def is_ready(self) -> bool:
        return self._connected


# ---------------------------------------------------------------------------
# Helper to build a can_use_tool control-request dict
# ---------------------------------------------------------------------------


def _can_use_tool_request(
    tool_name: str,
    tool_input: dict,
    request_id: str = "req-1",
) -> dict:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": tool_name,
            "input": tool_input,
            "permission_suggestions": [],
            "tool_use_id": "toolu_test",
        },
    }


# ---------------------------------------------------------------------------
# Helper: a simple pass-through can_use_tool that always allows
# ---------------------------------------------------------------------------


async def _allow_can_use_tool(
    tool_name: str,
    tool_input: dict,
    context: ToolPermissionContext,
) -> PermissionResultAllow:
    return PermissionResultAllow()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGovernanceHookAllow:
    """Governance hook that allows — tool execution proceeds normally."""

    @pytest.mark.anyio
    async def test_sync_allow_hook_passes_through(self):
        def allow_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=True)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=allow_policy,
        )

        await query._handle_control_request(
            _can_use_tool_request("Bash", {"command": "ls"})
        )

        assert len(transport.written_messages) == 1
        response = json.loads(transport.written_messages[0])
        assert response["response"]["response"]["behavior"] == "allow"

    @pytest.mark.anyio
    async def test_async_allow_hook_passes_through(self):
        async def async_allow_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=True)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=async_allow_policy,
        )

        await query._handle_control_request(
            _can_use_tool_request("Read", {"file_path": "/tmp/x"})
        )

        assert len(transport.written_messages) == 1
        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "allow"


class TestGovernanceHookBlock:
    """Governance hook that blocks — model receives rejection message."""

    @pytest.mark.anyio
    async def test_sync_block_hook_denies_tool(self):
        def block_all(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(
                allowed=False,
                reason="All tools blocked by policy",
            )

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=block_all,
        )

        await query._handle_control_request(
            _can_use_tool_request("Bash", {"command": "rm -rf /"})
        )

        assert len(transport.written_messages) == 1
        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "deny"
        assert "All tools blocked by policy" in result["message"]

    @pytest.mark.anyio
    async def test_async_block_hook_denies_tool(self):
        async def async_block(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=False, reason="Async block")

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=async_block,
        )

        await query._handle_control_request(
            _can_use_tool_request("Write", {"file_path": "/etc/passwd"})
        )

        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "deny"
        assert result["message"] == "Async block"

    @pytest.mark.anyio
    async def test_block_without_reason_uses_default_message(self):
        def silent_block(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=False)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=silent_block,
        )

        await query._handle_control_request(_can_use_tool_request("Bash", {}))

        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "deny"
        assert result["message"]  # non-empty default message

    @pytest.mark.anyio
    async def test_block_hook_skips_can_use_tool(self):
        """can_use_tool must NOT be called when governance hook blocks."""
        can_use_tool_called = False

        async def tracking_can_use_tool(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> PermissionResultAllow:
            nonlocal can_use_tool_called
            can_use_tool_called = True
            return PermissionResultAllow()

        def block_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=False, reason="blocked")

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=tracking_can_use_tool,
            governance_hook=block_policy,
        )

        await query._handle_control_request(_can_use_tool_request("Bash", {}))

        assert not can_use_tool_called, (
            "can_use_tool should not be called when governance hook blocks"
        )
        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "deny"


class TestGovernanceHookInputModification:
    """Governance hook that rewrites tool input before execution."""

    @pytest.mark.anyio
    async def test_modified_input_is_used(self):
        used_input: dict = {}

        async def tracking_can_use_tool(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> PermissionResultAllow:
            used_input.update(tool_input)
            return PermissionResultAllow()

        def sanitize_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            new_input = dict(tool_input)
            new_input["command"] = "ls -la /tmp"  # override dangerous command
            return GovernanceDecision(allowed=True, modified_input=new_input)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=tracking_can_use_tool,
            governance_hook=sanitize_policy,
        )

        await query._handle_control_request(
            _can_use_tool_request("Bash", {"command": "rm -rf /"})
        )

        # can_use_tool should have received the modified input
        assert used_input.get("command") == "ls -la /tmp"
        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "allow"

    @pytest.mark.anyio
    async def test_modified_input_appears_in_updated_input(self):
        def add_flag(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(
                allowed=True,
                modified_input={**tool_input, "safe_mode": True},
            )

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=add_flag,
        )

        await query._handle_control_request(
            _can_use_tool_request("Write", {"file_path": "/tmp/out.txt"})
        )

        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "allow"
        assert result["updatedInput"].get("safe_mode") is True

    @pytest.mark.anyio
    async def test_no_modified_input_uses_original(self):
        """When modified_input is absent the original input is forwarded unchanged."""

        def noop_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=True)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=noop_policy,
        )

        await query._handle_control_request(
            _can_use_tool_request("Read", {"file_path": "/tmp/data.txt"})
        )

        result = json.loads(transport.written_messages[0])["response"]["response"]
        assert result["behavior"] == "allow"
        assert result["updatedInput"]["file_path"] == "/tmp/data.txt"


class TestGovernanceHookContextPropagation:
    """Governance hook receives the same ToolPermissionContext as can_use_tool."""

    @pytest.mark.anyio
    async def test_context_fields_are_forwarded(self):
        received: dict = {}

        def capture_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            received["tool_name"] = tool_name
            received["tool_input"] = tool_input
            received["tool_use_id"] = context.tool_use_id
            return GovernanceDecision(allowed=True)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=capture_policy,
        )

        request = {
            "type": "control_request",
            "request_id": "req-ctx",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "echo hi"},
                "permission_suggestions": [],
                "tool_use_id": "toolu_ctx_test",
            },
        }
        await query._handle_control_request(request)

        assert received["tool_name"] == "Bash"
        assert received["tool_input"] == {"command": "echo hi"}
        assert received["tool_use_id"] == "toolu_ctx_test"


class TestGovernanceHookToolFiltering:
    """Governance hook can selectively block specific tools."""

    @pytest.mark.anyio
    async def test_selective_block_by_tool_name(self):
        def block_bash(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            if tool_name == "Bash":
                return GovernanceDecision(allowed=False, reason="Bash is disabled")
            return GovernanceDecision(allowed=True)

        transport = MockTransport()
        query = Query(
            transport=transport,
            is_streaming_mode=True,
            can_use_tool=_allow_can_use_tool,
            governance_hook=block_bash,
        )

        # Bash should be blocked
        await query._handle_control_request(
            _can_use_tool_request("Bash", {"command": "ls"}, "req-bash")
        )
        bash_result = json.loads(transport.written_messages[0])["response"]["response"]
        assert bash_result["behavior"] == "deny"

        # Read should be allowed
        await query._handle_control_request(
            _can_use_tool_request("Read", {"file_path": "/tmp/x"}, "req-read")
        )
        read_result = json.loads(transport.written_messages[1])["response"]["response"]
        assert read_result["behavior"] == "allow"


class TestGovernanceHookClaudeAgentOptions:
    """Integration: GovernanceDecision types are importable and options accept the hook."""

    def test_governance_decision_is_exported(self):
        from claude_agent_sdk import GovernanceDecision

        d = GovernanceDecision(allowed=True)
        assert d["allowed"] is True

        d2 = GovernanceDecision(allowed=False, reason="no", modified_input={})
        assert d2["allowed"] is False
        assert d2["reason"] == "no"
        assert d2["modified_input"] == {}

        # GovernanceHook is a type alias (callable) – just verify it's importable
        assert GovernanceHook is not None

    def test_options_accepts_governance_hook(self):
        def my_policy(
            tool_name: str,
            tool_input: dict,
            context: ToolPermissionContext,
        ) -> GovernanceDecision:
            return GovernanceDecision(allowed=True)

        options = ClaudeAgentOptions(governance_hook=my_policy)
        assert options.governance_hook is my_policy

    def test_options_governance_hook_defaults_to_none(self):
        options = ClaudeAgentOptions()
        assert options.governance_hook is None
