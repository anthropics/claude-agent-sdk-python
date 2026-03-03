"""Claude Hook SDK for Python."""

from ._version import __version__
from .types import (
    # Hook outputs
    AsyncHookJSONOutput,
    # Hook inputs
    BaseHookInput,
    # Hook callback and context
    HookCallback,
    HookContext,
    # Hook event type
    HookEvent,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    HookSpecificOutput,
    NotificationHookInput,
    NotificationHookSpecificOutput,
    PermissionRequestHookInput,
    PermissionRequestHookSpecificOutput,
    PostToolUseFailureHookInput,
    PostToolUseFailureHookSpecificOutput,
    PostToolUseHookInput,
    PostToolUseHookSpecificOutput,
    PreCompactHookInput,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
    SessionStartHookSpecificOutput,
    StopHookInput,
    SubagentStartHookInput,
    SubagentStartHookSpecificOutput,
    SubagentStopHookInput,
    SyncHookJSONOutput,
    UserPromptSubmitHookInput,
    UserPromptSubmitHookSpecificOutput,
)

__all__ = [
    "__version__",
    # Hook event type
    "HookEvent",
    # Hook inputs
    "BaseHookInput",
    "HookInput",
    "NotificationHookInput",
    "PermissionRequestHookInput",
    "PostToolUseFailureHookInput",
    "PostToolUseHookInput",
    "PreCompactHookInput",
    "PreToolUseHookInput",
    "StopHookInput",
    "SubagentStartHookInput",
    "SubagentStopHookInput",
    "UserPromptSubmitHookInput",
    # Hook outputs
    "AsyncHookJSONOutput",
    "HookJSONOutput",
    "HookSpecificOutput",
    "NotificationHookSpecificOutput",
    "PermissionRequestHookSpecificOutput",
    "PostToolUseFailureHookSpecificOutput",
    "PostToolUseHookSpecificOutput",
    "PreToolUseHookSpecificOutput",
    "SessionStartHookSpecificOutput",
    "SubagentStartHookSpecificOutput",
    "SyncHookJSONOutput",
    "UserPromptSubmitHookSpecificOutput",
    # Hook callback and context
    "HookCallback",
    "HookContext",
    "HookMatcher",
]
