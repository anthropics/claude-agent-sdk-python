# Changelog

## 0.1.39

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.49

## 0.1.38

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.47

## 0.1.37

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.44

## 0.1.36

### New Features

- **Thinking configuration**: Added `ThinkingConfig` types (`ThinkingConfigAdaptive`, `ThinkingConfigEnabled`, `ThinkingConfigDisabled`) and `thinking` field to `ClaudeAgentOptions` for fine-grained control over extended thinking behavior. The new `thinking` field takes precedence over the now-deprecated `max_thinking_tokens` field (#565)
- **Effort option**: Added `effort` field to `ClaudeAgentOptions` supporting `"low"`, `"medium"`, `"high"`, and `"max"` values for controlling thinking depth (#565)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.42

## 0.1.35

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.39

## 0.1.34

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.38
- Updated CI workflows to use Claude Opus 4.6 model (#556)

## 0.1.33

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.37

## 0.1.32

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.36

## 0.1.31

### New Features

- **MCP tool annotations support**: Added support for MCP tool annotations via the `@tool` decorator's new `annotations` parameter, allowing developers to specify metadata hints like `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`. Re-exported `ToolAnnotations` from `claude_agent_sdk` for convenience (#551)

### Bug Fixes

- **Large agent definitions**: Fixed an issue where large agent definitions would silently fail to register due to platform-specific CLI argument size limits (ARG_MAX). Agent definitions are now sent via the initialize control request through stdin, matching the TypeScript SDK approach and allowing arbitrarily large agent payloads (#468)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.33

## 0.1.30

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.32

## 0.1.29

### New Features

- **New hook events**: Added support for three new hook event types (#545):
  - `Notification` — for handling notification events with `NotificationHookInput` and `NotificationHookSpecificOutput`
  - `SubagentStart` — for handling subagent startup with `SubagentStartHookInput` and `SubagentStartHookSpecificOutput`
  - `PermissionRequest` — for handling permission requests with `PermissionRequestHookInput` and `PermissionRequestHookSpecificOutput`

- **Enhanced hook input/output types**: Added missing fields to existing hook types (#545):
  - `PreToolUseHookInput`: added `tool_use_id`
  - `PostToolUseHookInput`: added `tool_use_id`
  - `SubagentStopHookInput`: added `agent_id`, `agent_transcript_path`, `agent_type`
  - `PreToolUseHookSpecificOutput`: added `additionalContext`
  - `PostToolUseHookSpecificOutput`: added `updatedMCPToolOutput`

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.31

## 0.1.28

### Bug Fixes

- **AssistantMessage error field**: Fixed `AssistantMessage.error` field not being populated due to incorrect data path. The error field is now correctly read from the top level of the response (#506)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.30

## 0.1.27

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.29

## 0.1.26

### New Features

- **PostToolUseFailure hook event**: Added `PostToolUseFailure` hook event type for handling tool use failures, including `PostToolUseFailureHookInput` and `PostToolUseFailureHookSpecificOutput` types (#535)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.27

## 0.1.25

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.23

## 0.1.24

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.22

## 0.1.23

### Features

- **MCP status querying**: Added public `get_mcp_status()` method to `ClaudeSDKClient` for querying MCP server connection status without accessing private internals (#516)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.20

## 0.1.22

### Features

- Added `tool_use_result` field to `UserMessage` (#495)

### Bug Fixes

- Added permissions to release job in auto-release workflow (#504)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.19
- Extracted build-and-publish workflow into reusable component (#488)

## 0.1.21

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.15

## 0.1.20

### Bug Fixes

- **Permission callback test reliability**: Improved robustness of permission callback end-to-end tests (#485)

### Documentation

- Updated Claude Agent SDK documentation link (#442)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.9
- **CI improvements**: Updated claude-code actions from @beta to @v1 (#467)

## 0.1.19

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.1
- **CI improvements**: Jobs requiring secrets now skip when running from forks (#451)
- Fixed YAML syntax error in create-release-tag workflow (#429)

## 0.1.18

### Internal/Other Changes

- **Docker-based test infrastructure**: Added Docker support for running e2e tests in containerized environments, helping catch Docker-specific issues (#424)
- Updated bundled Claude CLI to version 2.0.72

## 0.1.17

### New Features

- **UserMessage UUID field**: Added `uuid` field to `UserMessage` response type, making it easier to use the `rewind_files()` method by providing direct access to message identifiers needed for file checkpointing (#418)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.70

## 0.1.16

### Bug Fixes

- **Rate limit detection**: Fixed parsing of the `error` field in `AssistantMessage`, enabling applications to detect and handle API errors like rate limits. Previously, the `error` field was defined but never populated from CLI responses (#405)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.68

## 0.1.15

### New Features

- **File checkpointing and rewind**: Added `enable_file_checkpointing` option to `ClaudeAgentOptions` and `rewind_files(user_message_id)` method to `ClaudeSDKClient` and `Query`. This enables reverting file changes made during a session back to a specific checkpoint, useful for exploring different approaches or recovering from unwanted modifications (#395)

### Documentation

- Added license and terms section to README (#399)

## 0.1.14

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.62

## 0.1.13

### Bug Fixes

- **Faster error handling**: CLI errors (e.g., invalid session ID) now propagate to pending requests immediately instead of waiting for the 60-second timeout (#388)
- **Pydantic 2.12+ compatibility**: Fixed `PydanticUserError` caused by `McpServer` type only being imported under `TYPE_CHECKING` (#385)
- **Concurrent subagent writes**: Added write lock to prevent `BusyResourceError` when multiple subagents invoke MCP tools in parallel (#391)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.59

## 0.1.12

### New Features

- **Tools option**: Added `tools` option to `ClaudeAgentOptions` for controlling the base set of available tools, matching the TypeScript SDK functionality. Supports three modes:
  - Array of tool names to specify which tools should be available (e.g., `["Read", "Edit", "Bash"]`)
  - Empty array `[]` to disable all built-in tools
  - Preset object `{"type": "preset", "preset": "claude_code"}` to use the default Claude Code toolset
- **SDK beta support**: Added `betas` option to `ClaudeAgentOptions` for enabling Anthropic API beta features. Currently supports `"context-1m-2025-08-07"` for extended context window

## 0.1.11

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.57

## 0.1.10

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.53

## 0.1.9

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.0.49

## 0.1.8

### Features

- Claude Code is now included by default in the package, removing the requirement to install it separately. If you do wish to use a separately installed build, use the `cli_path` field in `Options`.

## 0.1.7

### Features

- **Structured outputs support**: Agents can now return validated JSON matching your schema. See https://docs.claude.com/en/docs/agent-sdk/structured-outputs. (#340)
- **Fallback model handling**: Added automatic fallback model handling for improved reliability and parity with the TypeScript SDK. When the primary model is unavailable, the SDK will automatically use a fallback model (#317)
- **Local Claude CLI support**: Added support for using a locally installed Claude CLI from `~/.claude/local/claude`, enabling development and testing with custom Claude CLI builds (#302)

## 0.1.6

### Features

- **Max budget control**: Added `max_budget_usd` option to set a maximum spending limit in USD for SDK sessions. When the budget is exceeded, the session will automatically terminate, helping prevent unexpected costs (#293)
- **Extended thinking configuration**: Added `max_thinking_tokens` option to control the maximum number of tokens allocated for Claude's internal reasoning process. This allows fine-tuning of the balance between response quality and token usage (#298)

### Bug Fixes

- **System prompt defaults**: Fixed issue where a default system prompt was being used when none was specified. The SDK now correctly uses an empty system prompt by default, giving users full control over agent behavior (#290)

## 0.1.5

### Features

- **Plugin support**: Added the ability to load Claude Code plugins programmatically through the SDK. Plugins can be specified using the new `plugins` field in `ClaudeAgentOptions` with a `SdkPluginConfig` type that supports loading local plugins by path. This enables SDK applications to extend functionality with custom commands and capabilities defined in plugin directories

## 0.1.4

### Features

- **Skip version check**: Added `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK` environment variable to allow users to disable the Claude Code version check. Set this environment variable to skip the minimum version validation when the SDK connects to Claude Code. (Only recommended if you already have Claude Code 2.0.0 or higher installed, otherwise some functionality may break)
- SDK MCP server tool calls can now return image content blocks

## 0.1.3

### Features

- **Strongly-typed hook inputs**: Added typed hook input structures (`PreToolUseHookInput`, `PostToolUseHookInput`, `UserPromptSubmitHookInput`, etc.) using TypedDict for better IDE autocomplete and type safety. Hook callbacks now receive fully typed input parameters

### Bug Fixes

- **Hook output field conversion**: Fixed bug where Python-safe field names (`async_`, `continue_`) in hook outputs were not being converted to CLI format (`async`, `continue`). This caused hook control fields to be silently ignored, preventing proper hook behavior. The SDK now automatically converts field names when communicating with the CLI

### Internal/Other Changes

- **CI/CD**: Re-enabled Windows testing in the end-to-end test workflow. Windows CI had been temporarily disabled but is now fully operational across all test suites

## 0.1.2

### Bug Fixes

- **Hook output fields**: Added missing hook output fields to match the TypeScript SDK, including `reason`, `continue_`, `suppressOutput`, and `stopReason`. The `decision` field now properly supports both "approve" and "block" values. Added `AsyncHookJSONOutput` type for deferred hook execution and proper typing for `hookSpecificOutput` with discriminated unions

## 0.1.1

### Features

- **Minimum Claude Code version check**: Added version validation to ensure Claude Code 2.0.0+ is installed. The SDK will display a warning if an older version is detected, helping prevent compatibility issues
- **Updated PermissionResult types**: Aligned permission result types with the latest control protocol for better type safety and compatibility

### Improvements

- **Model references**: Updated all examples and tests to use the simplified `claude-sonnet-4-5` model identifier instead of dated version strings

## 0.1.0

Introducing the Claude Agent SDK! The Claude Code SDK has been renamed to better reflect its capabilities for building AI agents across all domains, not just coding.

### Breaking Changes

#### Type Name Changes

- **ClaudeCodeOptions renamed to ClaudeAgentOptions**: The options type has been renamed to match the new SDK branding. Update all imports and type references:

  ```python
  # Before
  from claude_agent_sdk import query, ClaudeCodeOptions
  options = ClaudeCodeOptions(...)

  # After
  from claude_agent_sdk import query, ClaudeAgentOptions
  options = ClaudeAgentOptions(...)
  ```

#### System Prompt Changes

- **Merged prompt options**: The `custom_system_prompt` and `append_system_prompt` fields have been merged into a single `system_prompt` field for simpler configuration
- **No default system prompt**: The Claude Code system prompt is no longer included by default, giving you full control over agent behavior. To use the Claude Code system prompt, explicitly set:
  ```python
  system_prompt={"type": "preset", "preset": "claude_code"}
  ```

#### Settings Isolation

- **No filesystem settings by default**: Settings files (`settings.json`, `CLAUDE.md`), slash commands, and subagents are no longer loaded automatically. This ensures SDK applications have predictable behavior independent of local filesystem configurations
- **Explicit settings control**: Use the new `setting_sources` field to specify which settings locations to load: `["user", "project", "local"]`

For full migration instructions, see our [migration guide](https://docs.claude.com/en/docs/claude-code/sdk/migration-guide).

### New Features

- **Programmatic subagents**: Subagents can now be defined inline in code using the `agents` option, enabling dynamic agent creation without filesystem dependencies. [Learn more](https://docs.claude.com/en/api/agent-sdk/subagents)
- **Session forking**: Resume sessions with the new `fork_session` option to branch conversations and explore different approaches from the same starting point. [Learn more](https://docs.claude.com/en/api/agent-sdk/sessions)
- **Granular settings control**: The `setting_sources` option gives you fine-grained control over which filesystem settings to load, improving isolation for CI/CD, testing, and production deployments

### Documentation

- Comprehensive documentation now available in the [API Guide](https://docs.claude.com/en/api/agent-sdk/overview)
- New guides for [Custom Tools](https://docs.claude.com/en/api/agent-sdk/custom-tools), [Permissions](https://docs.claude.com/en/api/agent-sdk/permissions), [Session Management](https://docs.claude.com/en/api/agent-sdk/sessions), and more
- Complete [Python API reference](https://docs.claude.com/en/api/agent-sdk/python)

## 0.0.22

- Introduce custom tools, implemented as in-process MCP servers.
- Introduce hooks.
- Update internal `Transport` class to lower-level interface.
- `ClaudeSDKClient` can no longer be run in different async contexts.

## 0.0.19

- Add `ClaudeCodeOptions.add_dirs` for `--add-dir`
- Fix ClaudeCodeSDK hanging when MCP servers log to Claude Code stderr

## 0.0.18

- Add `ClaudeCodeOptions.settings` for `--settings`

## 0.0.17

- Remove dependency on asyncio for Trio compatibility

## 0.0.16

- Introduce ClaudeSDKClient for bidirectional streaming conversation
- Support Message input, not just string prompts, in query()
- Raise explicit error if the cwd does not exist

## 0.0.14

- Add safety limits to Claude Code CLI stderr reading
- Improve handling of output JSON messages split across multiple stream reads

## 0.0.13

- Update MCP (Model Context Protocol) types to align with Claude Code expectations
- Fix multi-line buffering issue
- Rename cost_usd to total_cost_usd in API responses
- Fix optional cost fields handling
