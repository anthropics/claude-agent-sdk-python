"""End-to-end tests for the permission_prompts option with real Claude API calls.

By default the host answers permission prompts through ``can_use_tool``.
``permission_prompts="none"`` passes ``--permission-prompts none``: anything
that would prompt is denied at once, ``can_use_tool`` is never called, and
the session carries on. These tests ask Claude to write a file, which needs
approval in the default permission mode.
"""

from pathlib import Path

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    ResultMessage,
    ToolPermissionContext,
    query,
)

PROMPT = (
    "Use the Write tool to create out.txt containing the word hi. "
    "If the tool is denied, reply DENIED and stop."
)


def _options(cwd: Path, **kwargs) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(cwd),
        model="haiku",
        tools=["Write"],
        permission_mode="default",
        setting_sources=[],
        extra_args={"strict-mcp-config": None},
        max_turns=3,
        **kwargs,
    )


def _approving_callback(asked: list[str]):
    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow:
        asked.append(tool_name)
        return PermissionResultAllow()

    return can_use_tool


async def _run_client(options: ClaudeAgentOptions) -> None:
    async with ClaudeSDKClient(options=options) as client:
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                assert not message.is_error, message


@pytest.mark.e2e
@pytest.mark.anyio
async def test_host_answers_prompts_by_default(tmp_path):
    """Control: without the option the write asks can_use_tool and lands, so
    the tests below are meaningful."""
    asked: list[str] = []
    await _run_client(_options(tmp_path, can_use_tool=_approving_callback(asked)))
    assert "Write" in asked
    assert (tmp_path / "out.txt").exists()


@pytest.mark.e2e
@pytest.mark.anyio
async def test_client_none_denies_without_asking_host(tmp_path):
    asked: list[str] = []
    await _run_client(
        _options(
            tmp_path,
            can_use_tool=_approving_callback(asked),
            permission_prompts="none",
        )
    )
    assert asked == []
    assert not (tmp_path / "out.txt").exists()


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_none_denies_and_finishes(tmp_path):
    async for message in query(
        prompt=PROMPT, options=_options(tmp_path, permission_prompts="none")
    ):
        if isinstance(message, ResultMessage):
            assert not message.is_error, message
    assert not (tmp_path / "out.txt").exists()
