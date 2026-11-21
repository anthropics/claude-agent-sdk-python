"""Deferred tools must consult Python hooks again when a session resumes."""

import shlex
from pathlib import Path
from typing import Any

import anyio
import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    query,
)


@pytest.mark.e2e
@pytest.mark.anyio
@pytest.mark.parametrize("resume_api", ["query", "client"])
async def test_deferred_tool_resume_reruns_pre_tool_use(
    tmp_path: Path, resume_api: str
):
    marker = tmp_path / "executed.txt"
    calls: list[tuple[str, str | None]] = []

    async def defer_hook(data: Any, tool_use_id: str | None, context: Any):
        calls.append(("defer", tool_use_id))
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "defer",
                "permissionDecisionReason": "Wait for approval",
            }
        }

    async def deny_hook(data: Any, tool_use_id: str | None, context: Any):
        calls.append(("deny", tool_use_id))
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Approval was denied",
            }
        }

    def options(hook: Any, resume: str | None = None):
        return ClaudeAgentOptions(
            cwd=tmp_path,
            env={"CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
            setting_sources=[],
            tools=["Bash"],
            allowed_tools=["Bash"],
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[hook])]},
            max_turns=3,
            resume=resume,
        )

    async def empty_prompt():
        if False:
            yield {}

    command = f"echo resumed > {shlex.quote(marker.as_posix())}"
    first_result = None
    resumed_result = None
    with anyio.fail_after(120):
        async for message in query(
            prompt=f"Run this exact Bash command once: {command}",
            options=options(defer_hook),
        ):
            if isinstance(message, ResultMessage):
                first_result = message

        assert first_result is not None
        assert first_result.stop_reason == "tool_deferred"
        assert first_result.deferred_tool_use is not None
        assert not marker.exists()

        resumed_options = options(deny_hook, first_result.session_id)
        if resume_api == "query":
            async for message in query(prompt=empty_prompt(), options=resumed_options):
                if isinstance(message, ResultMessage):
                    resumed_result = message
        else:
            async with ClaudeSDKClient(options=resumed_options) as client:
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        resumed_result = message

    assert resumed_result is not None
    assert not resumed_result.is_error
    assert not marker.exists(), "The resumed tool ran despite the deny hook"
    assert ("deny", first_result.deferred_tool_use.id) in calls
