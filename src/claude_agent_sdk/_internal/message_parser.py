"""Message parser for Claude Code SDK responses."""

import json
import logging
from typing import Any

from .._errors import MessageParseError, get_api_error_class
from ..types import (
    AssistantMessage,
    ContentBlock,
    Message,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

logger = logging.getLogger(__name__)

# Common wrapper keys that models may add around structured output
# See: https://github.com/anthropics/claude-agent-sdk-python/issues/502
_WRAPPER_KEYS = frozenset({"output", "response", "json", "data", "result"})


def _normalize_structured_output(value: Any) -> Any:
    """Normalize structured output by unwrapping common wrapper keys and parsing stringified JSON.

    This handles two common issues with model-generated structured output:

    1. Wrapper keys (#502): Model wraps data in {"output": {...}}, {"response": {...}}, etc.
       We unwrap these to return just the inner data.

    2. Stringified JSON (#510): Model serializes arrays/objects as JSON strings like
       "[{\\"field\\": ...}]" instead of native arrays. We parse these back to native types.

    Args:
        value: The raw structured_output value from the CLI

    Returns:
        Normalized structured output with wrappers removed and strings parsed
    """
    if value is None:
        return None

    # Handle wrapper keys: {"output": {...}} -> {...}
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value.keys()))
        if key.lower() in _WRAPPER_KEYS:
            logger.debug(f"Unwrapping structured_output from '{key}' wrapper")
            value = value[key]

    # Recursively normalize the value (handles nested stringified JSON)
    return _parse_stringified_json(value)


def _parse_stringified_json(value: Any) -> Any:
    """Recursively parse stringified JSON values back to native Python types.

    Handles cases where the model serializes arrays/objects as strings:
    - "[{\\"field\\": \\"value\\"}]" -> [{"field": "value"}]
    - "{\\"key\\": \\"value\\"}" -> {"key": "value"}

    Args:
        value: Any value that may contain stringified JSON

    Returns:
        Value with stringified JSON parsed to native types
    """
    if isinstance(value, str):
        # Try to parse strings that look like JSON arrays or objects
        stripped = value.strip()
        if (stripped.startswith("[") and stripped.endswith("]")) or (
            stripped.startswith("{") and stripped.endswith("}")
        ):
            try:
                parsed = json.loads(value)
                logger.debug("Parsed stringified JSON in structured_output")
                # Recursively normalize the parsed value
                return _parse_stringified_json(parsed)
            except json.JSONDecodeError:
                # Not valid JSON, return as-is
                pass
        return value

    if isinstance(value, dict):
        return {k: _parse_stringified_json(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_parse_stringified_json(item) for item in value]

    return value


def parse_message(data: dict[str, Any]) -> Message:
    """
    Parse message from CLI output into typed Message objects.

    Args:
        data: Raw message dictionary from CLI output

    Returns:
        Parsed Message object

    Raises:
        MessageParseError: If parsing fails or message type is unrecognized
    """
    if not isinstance(data, dict):
        raise MessageParseError(
            f"Invalid message data type (expected dict, got {type(data).__name__})",
            data,
        )

    message_type = data.get("type")
    if not message_type:
        raise MessageParseError("Message missing 'type' field", data)

    match message_type:
        case "user":
            try:
                parent_tool_use_id = data.get("parent_tool_use_id")
                tool_use_result = data.get("tool_use_result")
                uuid = data.get("uuid")
                if isinstance(data["message"]["content"], list):
                    user_content_blocks: list[ContentBlock] = []
                    for block in data["message"]["content"]:
                        match block["type"]:
                            case "text":
                                user_content_blocks.append(
                                    TextBlock(text=block["text"])
                                )
                            case "tool_use":
                                user_content_blocks.append(
                                    ToolUseBlock(
                                        id=block["id"],
                                        name=block["name"],
                                        input=block["input"],
                                    )
                                )
                            case "tool_result":
                                user_content_blocks.append(
                                    ToolResultBlock(
                                        tool_use_id=block["tool_use_id"],
                                        content=block.get("content"),
                                        is_error=block.get("is_error"),
                                    )
                                )
                    return UserMessage(
                        content=user_content_blocks,
                        uuid=uuid,
                        parent_tool_use_id=parent_tool_use_id,
                        tool_use_result=tool_use_result,
                    )
                return UserMessage(
                    content=data["message"]["content"],
                    uuid=uuid,
                    parent_tool_use_id=parent_tool_use_id,
                    tool_use_result=tool_use_result,
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in user message: {e}", data
                ) from e

        case "assistant":
            try:
                content_blocks: list[ContentBlock] = []
                for block in data["message"]["content"]:
                    match block["type"]:
                        case "text":
                            content_blocks.append(TextBlock(text=block["text"]))
                        case "thinking":
                            content_blocks.append(
                                ThinkingBlock(
                                    thinking=block["thinking"],
                                    signature=block["signature"],
                                )
                            )
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

                # Error field is at top level, not inside message object
                # See: https://github.com/anthropics/claude-agent-sdk-python/issues/505
                error_type = data.get("error")
                model = data["message"]["model"]

                # Raise exception for API errors instead of returning them as messages
                # See: https://github.com/anthropics/claude-agent-sdk-python/issues/472
                if error_type is not None:
                    # Extract error message from content if available
                    error_message = "API error"
                    for block in content_blocks:
                        if isinstance(block, TextBlock) and block.text:
                            error_message = block.text
                            break

                    error_class = get_api_error_class(error_type)
                    raise error_class(error_message, model=model)

                return AssistantMessage(
                    content=content_blocks,
                    model=model,
                    parent_tool_use_id=data.get("parent_tool_use_id"),
                    error=error_type,
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in assistant message: {e}", data
                ) from e

        case "system":
            try:
                return SystemMessage(
                    subtype=data["subtype"],
                    data=data,
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in system message: {e}", data
                ) from e

        case "result":
            try:
                # Normalize structured_output to handle wrapper keys and stringified JSON
                # See: https://github.com/anthropics/claude-agent-sdk-python/issues/502
                # See: https://github.com/anthropics/claude-agent-sdk-python/issues/510
                raw_structured_output = data.get("structured_output")
                normalized_output = _normalize_structured_output(raw_structured_output)

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
                    structured_output=normalized_output,
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in result message: {e}", data
                ) from e

        case "stream_event":
            try:
                return StreamEvent(
                    uuid=data["uuid"],
                    session_id=data["session_id"],
                    event=data["event"],
                    parent_tool_use_id=data.get("parent_tool_use_id"),
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in stream_event message: {e}", data
                ) from e

        case _:
            raise MessageParseError(f"Unknown message type: {message_type}", data)
