"""Tests for ClaudeSDKClient.clear() and .resume() warm subprocess reuse."""

import pytest

from claude_agent_sdk import ClaudeSDKClient, CLIConnectionError
from claude_agent_sdk._internal.transport import Transport


class _NullTransport(Transport):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def write(self, data: str) -> None: ...
    async def end_input(self) -> None: ...
    def read_messages(self):
        async def _r():
            return
            yield

        return _r()

    def is_ready(self) -> bool:
        return False


def test_clear_raises_when_not_connected():
    client = ClaudeSDKClient()
    with pytest.raises(CLIConnectionError):
        client.clear()


def test_resume_raises_when_not_connected():
    client = ClaudeSDKClient()
    with pytest.raises(CLIConnectionError):
        client.resume("some-session")


def test_resume_raises_on_empty_session_id():
    client = ClaudeSDKClient()
    # Bypass connection check by patching internals
    client._query = object()
    client._transport = object()
    with pytest.raises(ValueError, match="non-empty"):
        client.resume("")
