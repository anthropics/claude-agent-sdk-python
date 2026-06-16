"""Regression tests for content-shape handling in parse_message.

These cover two shapes that previously raised an undocumented ``TypeError``
out of the parser instead of the documented ``MessageParseError`` (or parsing
cleanly):

1. an ``assistant`` message whose ``content`` is a plain string, and
2. a ``content`` list that contains a non-dict element.

Place under ``tests/`` (e.g. ``tests/test_message_parser_content_shapes.py``).
"""

import pytest

from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import AssistantMessage, TextBlock, UserMessage


def test_assistant_string_content_parses_like_user() -> None:
    """A string ``content`` should parse, matching the existing user branch."""
    msg = parse_message(
        {"type": "assistant", "message": {"model": "m", "content": "hello world"}}
    )
    assert isinstance(msg, AssistantMessage)
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextBlock)
    assert msg.content[0].text == "hello world"


def test_user_string_content_still_parses() -> None:
    msg = parse_message({"type": "user", "message": {"content": "hi"}})
    assert isinstance(msg, UserMessage)


def test_assistant_string_content_preserves_top_level_fields() -> None:
    msg = parse_message(
        {
            "type": "assistant",
            "message": {
                "model": "claude",
                "content": "plain",
                "id": "m1",
                "stop_reason": "end_turn",
            },
            "session_id": "s1",
        }
    )
    assert isinstance(msg, AssistantMessage)
    assert msg.model == "claude"
    assert msg.message_id == "m1"
    assert msg.stop_reason == "end_turn"
    assert msg.session_id == "s1"


@pytest.mark.parametrize("role", ["assistant", "user"])
def test_non_dict_content_block_raises_documented_error(role: str) -> None:
    """A non-dict block raises MessageParseError, never a raw TypeError."""
    message: dict[str, object] = {"content": ["oops"]}
    if role == "assistant":
        message["model"] = "m"
    with pytest.raises(MessageParseError):
        parse_message({"type": role, "message": message})


def test_normal_block_lists_unaffected() -> None:
    """Sanity: the common structured-content path is unchanged."""
    assistant = parse_message(
        {
            "type": "assistant",
            "message": {
                "model": "m",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                ],
            },
        }
    )
    assert isinstance(assistant, AssistantMessage)
    assert len(assistant.content) == 2

    user = parse_message(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
                ]
            },
        }
    )
    assert isinstance(user, UserMessage)
