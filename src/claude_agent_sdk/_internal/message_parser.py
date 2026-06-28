"""Message parser for Claude Code SDK responses."""

import logging
from typing import Any

from .._errors import MessageParseError
from ..types import (
    AssistantMessage,
    ContentBlock,
    DeferredToolUse,
    HookEventMessage,
    Message,
    MirrorErrorMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
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
        Parsed Message object

    Raises:
        MessageParseError: If parsing fails or message type is unrecognized
    """
    if not isinstance(data, dict):
        raise MessageParseError(
            f"Invalid message data type (expected dict, got {type(data).__name__})",
            data,
        )

    # Hook events (emitted when ``include_hook_events`` is enabled) arrive as
    # ``system`` messages with ``subtype`` of ``hook_started`` or
    # ``hook_response``. Route them to ``HookEventMessage`` before the generic
    # ``SystemMessage`` handling below.
    if data.get("type") == "system" and data.get("subtype") in (
        "hook_started",
        "hook_response",
    ):
        hook_event_name = (
            data.get("hook_event")
            or data.get("hook_name")
            or data.get("hook_event_name")
            or ""
        )
        return HookEventMessage(
            subtype=data["subtype"],
            hook_event_name=hook_event_name,
            data=data,
            session_id=data.get("session_id"),
            uuid=data.get("uuid"),
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
                        if not isinstance(block, dict):
                            raise MessageParseError(
                                f"Invalid content block (expected dict, got "
                                f"{type(block).__name__})",
                                data,
                            )
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
                raw_content = data["message"]["content"]
                if not isinstance(raw_content, list):
                    raise MessageParseError(
                        f"Invalid assistant content (expected list, got "
                        f"{type(raw_content).__name__})",
                        data,
                    )
                content_blocks: list[ContentBlock] = []
                for block in raw_content:
                    if not isinstance(block, dict):
                        raise MessageParseError(
                            f"Invalid content block (expected dict, got "
                            f"{type(block).__name__})",
                            data,
                        )
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
                        case "server_tool_use":
                            content_blocks.append(
                                ServerToolUseBlock(
                                    id=block["id"],
                                    name=block["name"],
                                    input=block["input"],
                                )
                            )
                        case "advisor_tool_result":
                            content_blocks.append(
                                ServerToolResultBlock(
                                    tool_use_id=block["tool_use_id"],
                                    content=block["content"],
                                )
                            )

                return AssistantMessage(
                    content=content_blocks,
                    model=data["message"]["model"],
                    parent_tool_use_id=data.get("parent_tool_use_id"),
                    error=data.get("error"),
                    usage=data["message"].get("usage"),
                    message_id=data["message"].get("id"),
                    stop_reason=data["message"].get("stop_reason"),
                    session_id=data.get("session_id"),
                    uuid=data.get("uuid"),
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in assistant message: {e}", data
                ) from e

        case "system":
            try:
                subtype = data["subtype"]
                match subtype:
                    case "task_started":
                        return TaskStartedMessage(
                            subtype=subtype,
                            data=data,
                            task_id=data["task_id"],
                            description=data["description"],
                            uuid=data["uuid"],
                            session_id=data["session_id"],
                            tool_use_id=data.get("tool_use_id"),
                            task_type=data.get("task_type"),
                        )
                    case "task_progress":
                        return TaskProgressMessage(
                            subtype=subtype,
                            data=data,
                            task_id=data["task_id"],
                            description=data["description"],
                            usage=data["usage"],
                            uuid=data["uuid"],
                            session_id=data["session_id"],
                            tool_use_id=data.get("tool_use_id"),
                            last_tool_name=data.get("last_tool_name"),
                        )
                    case "task_notification":
                        return TaskNotificationMessage(
                            subtype=subtype,
                            data=data,
                            task_id=data["task_id"],
                            status=data["status"],
                            output_file=data["output_file"],
                            summary=data["summary"],
                            uuid=data["uuid"],
                            session_id=data["session_id"],
                            tool_use_id=data.get("tool_use_id"),
                            usage=data.get("usage"),
                        )
                    case "task_updated":
                        # Terminal task completion sometimes arrives only as a
                        # task_updated patch (no separate task_notification), so
                        # expose it as a typed lifecycle message rather than a
                        # generic SystemMessage. Parsed defensively: the patch
                        # may omit uuid/session_id and parsing must never raise
                        # on a lifecycle event.
                        patch = data.get("patch")
                        if not isinstance(patch, dict):
                            patch = {}
                        # Terminal-ness is derived from patch.status; the CLI is
                        # assumed to set it on terminal transitions. A patch that
                        # carries only end_time/result/error (no status) is left
                        # non-terminal (status=None) — the full patch is still
                        # preserved on .patch for callers that need more.
                        return TaskUpdatedMessage(
                            subtype=subtype,
                            data=data,
                            task_id=data.get("task_id", ""),
                            patch=patch,
                            status=patch.get("status"),
                            session_id=data.get("session_id"),
                            uuid=data.get("uuid"),
                        )
                    case "mirror_error":
                        # SDK-synthesized via report_mirror_error — never emitted by the CLI subprocess.
                        return MirrorErrorMessage(
                            subtype=subtype,
                            data=data,
                            key=data.get("key"),
                            error=data.get("error", ""),
                        )
                    case _:
                        return SystemMessage(
                            subtype=subtype,
                            data=data,
                        )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in system message: {e}", data
                ) from e

        case "result":
            try:
                deferred = data.get("deferred_tool_use")
                return ResultMessage(
                    subtype=data["subtype"],
                    duration_ms=data["duration_ms"],
                    duration_api_ms=data["duration_api_ms"],
                    is_error=data["is_error"],
                    num_turns=data["num_turns"],
                    session_id=data["session_id"],
                    stop_reason=data.get("stop_reason"),
                    total_cost_usd=data.get("total_cost_usd"),
                    usage=data.get("usage"),
                    result=data.get("result"),
                    structured_output=data.get("structured_output"),
                    model_usage=data.get("modelUsage"),
                    permission_denials=data.get("permission_denials"),
                    deferred_tool_use=DeferredToolUse(
                        id=deferred["id"],
                        name=deferred["name"],
                        input=deferred["input"],
                    )
                    if deferred
                    else None,
                    errors=data.get("errors"),
                    api_error_status=data.get("api_error_status"),
                    uuid=data.get("uuid"),
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

        case "rate_limit_event":
            try:
                info = data["rate_limit_info"]
                return RateLimitEvent(
                    rate_limit_info=RateLimitInfo(
                        status=info["status"],
                        resets_at=info.get("resetsAt"),
                        rate_limit_type=info.get("rateLimitType"),
                        utilization=info.get("utilization"),
                        overage_status=info.get("overageStatus"),
                        overage_resets_at=info.get("overageResetsAt"),
                        overage_disabled_reason=info.get("overageDisabledReason"),
                        raw=info,
                    ),
                    uuid=data["uuid"],
                    session_id=data["session_id"],
                )
            except KeyError as e:
                raise MessageParseError(
                    f"Missing required field in rate_limit_event message: {e}", data
                ) from e

        case _:
            # Forward-compatible: skip unrecognized message types so newer
            # CLI versions don't crash older SDK versions.
            logger.debug("Skipping unknown message type: %s", message_type)
            return None
