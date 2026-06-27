"""Internal contextvar for propagating session context to SDK MCP tool handlers."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import ToolContext

_current_tool_context: contextvars.ContextVar[ToolContext | None] = (
    contextvars.ContextVar("_current_tool_context", default=None)
)
