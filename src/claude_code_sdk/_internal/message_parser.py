"""Message parser for Claude Code SDK responses."""

import logging
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

logger = logging.getLogger(__name__)


def parse_message(data: dict[str, Any]) -> Message | None:
    """
    Parse message from CLI output into typed Message objects.

    Args:
        data: Raw message dictionary from CLI output

    Returns:
        Parsed Message object or None if type is unrecognized or parsing fails
    """
    try:
        message_type = data.get("type")
        if not message_type:
            logger.warning("Message missing 'type' field: %s", data)
            return None

    except AttributeError:
        logger.error("Invalid message data type (expected dict): %s", type(data))
        return None

    match message_type:
        case "user":
            try:
                return UserMessage(content=data["message"]["content"])
            except KeyError as e:
                logger.error("Missing required field in user message: %s", e)
                return None

        case "assistant":
            try:
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
            except KeyError as e:
                logger.error("Missing required field in assistant message: %s", e)
                return None

        case "system":
            try:
                return SystemMessage(
                    subtype=data["subtype"],
                    data=data,
                )
            except KeyError as e:
                logger.error("Missing required field in system message: %s", e)
                return None

        case "result":
            try:
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
            except KeyError as e:
                logger.error("Missing required field in result message: %s", e)
                return None

        case _:
            logger.debug("Unknown message type: %s", message_type)
            return None
