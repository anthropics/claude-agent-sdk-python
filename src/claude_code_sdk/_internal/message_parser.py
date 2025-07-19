"""Message parser for Claude Code SDK responses."""

from typing import Any

from ..types import (
    AssistantMessage,
    ContentBlock,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def parse_message(data: dict[str, Any]) -> Message | None:
    """
    Parse message from CLI output into typed Message objects.

    Args:
        data: Raw message dictionary from CLI output

    Returns:
        Parsed Message object or None if type is unrecognized
    """
    match data["type"]:
        case "user":
            return UserMessage(content=data["message"]["content"])

        case "assistant":
            content_blocks: list[ContentBlock] = []
            for block in data["message"]["content"]:
                match block["type"]:
                    case "text":
                        content_blocks.append(TextBlock(text=block["text"]))
                    case "tool_use":
                        content_blocks.append(
                            ToolUseBlock(
                                id=block["id"],
                                name=block["name"],
                                input=block["input"],
                            )
                        )
                    case "tool_result":
                        content_blocks.append(
                            ToolResultBlock(
                                tool_use_id=block["tool_use_id"],
                                content=block.get("content"),
                                is_error=block.get("is_error"),
                            )
                        )

            return AssistantMessage(content=content_blocks)

        case "system":
            return SystemMessage(
                subtype=data["subtype"],
                data=data,
            )

        case "result":
            return ResultMessage(
                subtype=data["subtype"],
                duration_ms=data["duration_ms"],
                duration_api_ms=data["duration_api_ms"],
                is_error=data["is_error"],
                num_turns=data["num_turns"],
                session_id=data["session_id"],
                total_cost_usd=data.get("total_cost_usd"),
                usage=data.get("usage"),
                result=data.get("result"),
            )

        case _:
            return None
