# Changelog

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
