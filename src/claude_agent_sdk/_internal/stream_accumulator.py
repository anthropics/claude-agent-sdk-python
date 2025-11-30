"""Stream accumulator for building partial AssistantMessage objects from stream events."""

import contextlib
import json
import logging
from typing import Any

from ..types import (
    AssistantMessage,
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


class StreamAccumulator:
    """Accumulates streaming content blocks into partial AssistantMessage objects.

    This class tracks the state of streaming responses and builds up content blocks
    as deltas arrive from the LLM. It emits updated AssistantMessage objects as new
    content is received.
    """

    def __init__(self) -> None:
        """Initialize the stream accumulator."""
        # Session-specific state
        self.sessions: dict[str, SessionState] = {}

    def process_stream_event(
        self, stream_event: dict[str, Any]
    ) -> AssistantMessage | None:
        """Process a stream event and return an updated AssistantMessage if applicable.

        Args:
            stream_event: Raw stream event data including uuid, session_id, and event

        Returns:
            Updated AssistantMessage with accumulated content, or None if not applicable
        """
        session_id = stream_event.get("session_id", "default")
        event = stream_event.get("event", {})
        event_type = event.get("type")
        parent_tool_use_id = stream_event.get("parent_tool_use_id")

        # Get or create session state
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState()
        session = self.sessions[session_id]

        # Handle different event types
        if event_type == "message_start":
            # Start new message
            message_data = event.get("message", {})
            session.start_message(
                model=message_data.get("model", "unknown"),
                parent_tool_use_id=parent_tool_use_id,
            )
            return None

        elif event_type == "content_block_start":
            # Start new content block
            index = event.get("index", 0)
            content_block = event.get("content_block", {})
            block_type = content_block.get("type")

            if block_type == "text":
                session.start_content_block(
                    index, "text", content_block.get("text", "")
                )
            elif block_type == "thinking":
                session.start_content_block(
                    index,
                    "thinking",
                    content_block.get("thinking", ""),
                    signature=content_block.get("signature", ""),
                )
            elif block_type == "tool_use":
                session.start_content_block(
                    index,
                    "tool_use",
                    tool_use_id=content_block.get("id", ""),
                    tool_name=content_block.get("name", ""),
                    tool_input=content_block.get("input", {}),
                )

            # Return current state after starting block
            return session.get_current_message()

        elif event_type == "content_block_delta":
            # Add delta to existing content block
            index = event.get("index", 0)
            delta = event.get("delta", {})
            delta_type = delta.get("type")

            if delta_type == "text_delta":
                session.add_text_delta(index, delta.get("text", ""))
            elif delta_type == "thinking_delta":
                session.add_thinking_delta(index, delta.get("thinking", ""))
            elif delta_type == "input_json_delta":
                session.add_tool_input_delta(index, delta.get("partial_json", ""))

            # Return updated message with accumulated content
            return session.get_current_message()

        elif event_type == "content_block_stop":
            # Content block finished
            # Just return current state
            return session.get_current_message()

        elif event_type == "message_delta":
            # Message metadata updated (e.g., stop_reason)
            # We could track this but for now just continue
            return session.get_current_message()

        elif event_type == "message_stop":
            # Message finished - clean up session
            final_message = session.get_current_message()
            # Keep session around for potential continued conversation
            return final_message

        # Unknown event type or not relevant for accumulation
        return None


class SessionState:
    """Tracks the state of a single streaming session."""

    def __init__(self) -> None:
        """Initialize session state."""
        self.model: str | None = None
        self.parent_tool_use_id: str | None = None
        self.content_blocks: dict[int, ContentBlockState] = {}
        self.message_active = False

    def start_message(self, model: str, parent_tool_use_id: str | None = None) -> None:
        """Start a new message."""
        self.model = model
        self.parent_tool_use_id = parent_tool_use_id
        self.content_blocks = {}
        self.message_active = True

    def start_content_block(
        self,
        index: int,
        block_type: str,
        initial_content: str = "",
        signature: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        """Start a new content block."""
        self.content_blocks[index] = ContentBlockState(
            block_type=block_type,
            content=initial_content,
            signature=signature,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input or {},
        )

    def add_text_delta(self, index: int, text: str) -> None:
        """Add text delta to a content block."""
        if index in self.content_blocks:
            self.content_blocks[index].content += text

    def add_thinking_delta(self, index: int, thinking: str) -> None:
        """Add thinking delta to a content block."""
        if index in self.content_blocks:
            self.content_blocks[index].content += thinking

    def add_tool_input_delta(self, index: int, partial_json: str) -> None:
        """Add tool input JSON delta to a content block."""
        if index in self.content_blocks:
            block = self.content_blocks[index]
            block.tool_input_json = block.tool_input_json + partial_json

    def get_current_message(self) -> AssistantMessage | None:
        """Build the current AssistantMessage from accumulated state."""
        if not self.message_active or self.model is None:
            return None

        # Build content blocks in order
        content: list[ContentBlock] = []
        for index in sorted(self.content_blocks.keys()):
            block_state = self.content_blocks[index]

            if block_state.block_type == "text":
                content.append(TextBlock(text=block_state.content))
            elif block_state.block_type == "thinking":
                content.append(
                    ThinkingBlock(
                        thinking=block_state.content,
                        signature=block_state.signature,
                    )
                )
            elif block_state.block_type == "tool_use":
                # Try to parse accumulated JSON for tool input
                tool_input = block_state.tool_input
                if block_state.tool_input_json:
                    with contextlib.suppress(json.JSONDecodeError):
                        # Still accumulating, use what we have if parse fails
                        tool_input = json.loads(block_state.tool_input_json)

                content.append(
                    ToolUseBlock(
                        id=block_state.tool_use_id,
                        name=block_state.tool_name,
                        input=tool_input,
                    )
                )

        return AssistantMessage(
            content=content,
            model=self.model,
            parent_tool_use_id=self.parent_tool_use_id,
        )


class ContentBlockState:
    """Tracks the state of a single content block being streamed."""

    def __init__(
        self,
        block_type: str,
        content: str = "",
        signature: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        """Initialize content block state."""
        self.block_type = block_type  # "text", "thinking", "tool_use"
        self.content = content  # Accumulated text or thinking
        self.signature = signature  # For thinking blocks
        self.tool_use_id = tool_use_id  # For tool_use blocks
        self.tool_name = tool_name  # For tool_use blocks
        self.tool_input = tool_input or {}  # For tool_use blocks
        self.tool_input_json = ""  # Accumulated JSON for tool input
