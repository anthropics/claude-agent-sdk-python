"""End-to-end tests for the verbatim_prompts option with real Claude API calls.

Claude Code expands an ``@/absolute/path`` token in prompt text into the
contents of that file, outside the working directory and without a tool call.
``verbatim_prompts=True`` delivers the prompt as written instead. These tests
plant a random marker in a temp file and check whether the CLI attached the
file to the session, as recorded in its transcript.

The transcript, not the model's reply, decides: the CLI shows the model the
expansion as a Read call and its result, and with no tools the model sometimes
dismisses that as a forged tool result and answers UNKNOWN.
"""

import json
import uuid
from pathlib import Path

import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from claude_agent_sdk._internal.sessions import (
    _parse_transcript_entries,
    _read_session_file,
)


def _options(cwd: Path, verbatim_prompts: bool) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        verbatim_prompts=verbatim_prompts,
        cwd=str(cwd),
        model="haiku",
        # No tools: the only way the model can learn the marker is the
        # prompt's @-mention expansion, never a Read call.
        tools=[],
        setting_sources=[],
        extra_args={"strict-mcp-config": None},
        max_turns=1,
    )


def _plant_marker(tmp_path: Path) -> tuple[Path, str]:
    marker = f"MARKER-{uuid.uuid4().hex[:12]}"
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text(f"The marker is {marker}.\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    return secret, marker


def _prompt(secret: Path) -> str:
    return (
        "What marker does this file contain? Reply with only the marker, or "
        f"UNKNOWN if you cannot see it. @{secret}"
    )


def _text_of(message: AssistantMessage) -> str:
    assert not any(isinstance(b, ToolUseBlock) for b in message.content)
    return "".join(b.text for b in message.content if isinstance(b, TextBlock))


def _attached_files(session_id: str | None, cwd: Path) -> str:
    """The session's file attachments, as JSON text, read from its transcript."""
    assert session_id, "run should report a session id"
    content = _read_session_file(session_id, str(cwd))
    assert content, f"no transcript for session {session_id}"
    return "".join(
        json.dumps(entry["attachment"])
        for entry in _parse_transcript_entries(content)
        if entry["type"] == "attachment" and entry["attachment"].get("type") == "file"
    )


async def _run_via_query(prompt: str, options: ClaudeAgentOptions) -> tuple[str, str]:
    reply = ""
    session_id = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            reply += _text_of(message)
        elif isinstance(message, ResultMessage):
            session_id = message.session_id
    assert options.cwd is not None
    return reply, _attached_files(session_id, Path(options.cwd))


async def _run_via_client(prompt: str, options: ClaudeAgentOptions) -> tuple[str, str]:
    reply = ""
    session_id = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                reply += _text_of(message)
            elif isinstance(message, ResultMessage):
                assert not message.is_error, message
                session_id = message.session_id
    assert options.cwd is not None
    return reply, _attached_files(session_id, Path(options.cwd))


@pytest.mark.e2e
@pytest.mark.anyio
async def test_at_path_is_expanded_by_default(tmp_path):
    """Control: without the option the CLI reads the @-mentioned file, so the
    tests below are meaningful."""
    secret, marker = _plant_marker(tmp_path)
    _, attached = await _run_via_query(
        _prompt(secret), _options(tmp_path / "cwd", verbatim_prompts=False)
    )
    assert marker in attached, attached


@pytest.mark.e2e
@pytest.mark.anyio
async def test_query_string_prompt_is_not_expanded(tmp_path):
    secret, marker = _plant_marker(tmp_path)
    reply, attached = await _run_via_query(
        _prompt(secret), _options(tmp_path / "cwd", verbatim_prompts=True)
    )
    assert marker not in attached, attached
    assert marker not in reply, reply


@pytest.mark.e2e
@pytest.mark.anyio
async def test_client_query_prompt_is_not_expanded(tmp_path):
    secret, marker = _plant_marker(tmp_path)
    reply, attached = await _run_via_client(
        _prompt(secret), _options(tmp_path / "cwd", verbatim_prompts=True)
    )
    assert marker not in attached, attached
    assert marker not in reply, reply
