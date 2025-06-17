"""Internal client implementation."""

from collections.abc import AsyncIterator
from typing import Any

from ..types import (
    AssistantMessage,
    ClaudeCodeOptions,
    ContentBlock,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from .transport.subprocess_cli import SubprocessCLITransport


class InternalClient:
    """Internal client implementation."""

    def __init__(self) -> None:
        """Initialize the internal client."""

    async def process_query(
        self, prompt: str, options: ClaudeCodeOptions
    ) -> AsyncIterator[Message]:
        """Process a query through transport."""

        transport = SubprocessCLITransport(prompt=prompt, options=options)

        try:
            await transport.connect()

            async for data in transport.receive_messages():
                message = self._parse_message(data)
                if message:
                    yield message

        finally:
            await transport.disconnect()

    def _parse_message(self, data: dict[str, Any]) -> Message | None:
        """Parse message from CLI output, trusting the structure."""

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
