# Changelog

## 0.1.81

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.139

## 0.1.80

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.138

## 0.1.79

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.137

## 0.1.78

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.136

## 0.1.77

### Bug Fixes

- **Actionable error messages after error results**: Replaced the generic `Command failed with exit code 1` exception raised after an error result with one carrying the result's actual error text (e.g. "Reached maximum number of turns"), matching the TypeScript SDK behavior (#918)

### Documentation

- Deprecated `"Skill"` in `allowed_tools` in favor of the `skills` option on `ClaudeAgentOptions`, which provides more granular control over available skills (#924)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.133

## 0.1.76

### New Features

- **API error status on result messages**: Added `api_error_status: int | None` to `ResultMessage`, surfacing the HTTP status code (e.g. 429, 500, 529) from failing API calls. This provides a safe-to-log field for classifying API failures when `is_error=True` (#923)

### Bug Fixes

- **Permission suggestions deserialization**: Fixed `ToolPermissionContext.suggestions` containing raw dicts instead of `PermissionUpdate` instances. Added `PermissionUpdate.from_dict()` so suggestions from `can_use_tool` callbacks can be inspected and echoed back in `PermissionResultAllow(updated_permissions=...)` without `AttributeError` (#920)

### Internal/Other Changes

- Pinned third-party GitHub Actions to immutable commit SHAs (#919)
- Updated bundled Claude CLI to version 2.1.132

## 0.1.75

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.131

## 0.1.74

### New Features

- **Hook event streaming**: Added `include_hook_events` option to `ClaudeAgentOptions`. When set, hook events (PreToolUse, PostToolUse, Stop, etc.) are emitted by the CLI and yielded from the message stream as `HookEventMessage`, matching the TypeScript SDK's `includeHookEvents` (#917)
- **Defer hook decision**: Added support for the `"defer"` hook decision in `PreToolUseHookSpecificOutput.permissionDecision` and new `DeferredToolUse` dataclass on `ResultMessage.deferred_tool_use`, bringing parity with the TypeScript SDK's deferred tool use round trip (#865)
- **Strict MCP config**: Added `strict_mcp_config` option to `ClaudeAgentOptions`. When `True`, the CLI only uses MCP servers passed via `mcp_servers`, ignoring project, user, and global MCP configurations for fully deterministic server sets (#915)
- **Permission context enrichment**: Added `decision_reason`, `blocked_path`, `title`, `display_name`, and `description` fields to `ToolPermissionContext`, enabling richer permission prompts in `can_use_tool` callbacks (#909)
- **`updatedToolOutput` for post-tool hooks**: Added `updatedToolOutput` to `PostToolUseHookSpecificOutput` for replacing any tool's output before it reaches the model, not just MCP tools (#911)
- **`xhigh` effort level**: Added `"xhigh"` to the `effort` Literal on `ClaudeAgentOptions` and `AgentDefinition`, an Opus 4.7-specific level that falls back to `high` on other models (#914)
- **Subprocess cleanup on parent exit**: Registered an atexit handler to terminate live CLI subprocesses when the parent process exits, preventing orphaned `claude` processes from leaking (#916)

### Bug Fixes

- **ResourceWarning on disconnect**: Fixed `ResourceWarning: Unclosed <MemoryObjectReceiveStream>` emitted on `ClaudeSDKClient` disconnect and `query()` cleanup by closing the receive stream at the consumer boundary (#908)
- **Session `created_at` timestamp**: Fixed `list_sessions()` returning `created_at=None` for sessions whose first JSONL record lacks a `timestamp` field by scanning the full head buffer instead of only the first line (#907)

### Documentation

- Clarified that `can_use_tool` fires only on `"ask"` permission decisions, not on `"allow"` or `"deny"` (#912)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.129

## 0.1.73

### New Features

- **Eager session store flushing**: Added `session_store_flush` option to `ClaudeAgentOptions` (`"batched"` or `"eager"`). When set to `"eager"`, the transcript mirror delivers frames to `SessionStore.append()` in near-real-time instead of waiting for the end-of-turn flush, enabling live-tailing UIs, cross-process resume, and crash-durability use cases (#905)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.128

## 0.1.72

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.126

## 0.1.71

### New Features

- **Domain allowlist fields for sandbox network config**: Added `allowedDomains`, `deniedDomains`, `allowManagedDomainsOnly`, and `allowMachLookup` fields to `SandboxNetworkConfig`, bringing parity with the TypeScript schema and enabling Python SDK users to configure network allowlists with proper type hints (#893)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.123

## 0.1.70

### Bug Fixes

- **In-process MCP tool results silently lost with older `mcp` versions**: Bumped the `mcp` dependency floor to `>=1.19.0`. Older versions mishandled `CallToolResult` returns from SDK MCP tool handlers, causing the model to receive a validation-error blob instead of the actual tool output (#891)
- **Trio nursery corruption on early cancellation**: Fixed `RuntimeError: Nursery stack corrupted` when breaking out of `query()` iteration inside a trio nursery with `options.stderr` set. The stderr reader now uses `spawn_detached()` instead of manually managing a task group, matching the approach already used for the read loop (#885)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.122

## 0.1.69

### Documentation

- Added docstrings to `ClaudeAgentOptions` fields for improved IDE autocompletion and inline documentation (#873)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.121

## 0.1.68

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.119

## 0.1.67

### Bug Fixes

- **Trio compatibility restored**: Fixed `RuntimeError: no running event loop` when using `ClaudeSDKClient` or `query()` under trio, a regression introduced in v0.1.51. Uses sniffio-based dispatch to select the correct async primitive (`asyncio.Task` vs `trio.lowlevel.spawn_system_task`) at runtime while preserving the asyncio CPU-spin and cancel-scope fixes from #746 (#870)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.120
- Added `sniffio>=1.0.0` as an explicit runtime dependency (already a transitive dep of anyio)

## 0.1.66

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.119

## 0.1.65

### New Features

- **Batch session summaries**: Added `SessionStore.list_session_summaries()` optional protocol method and `fold_session_summary()` helper for O(1)-per-session list views. Stores that maintain append-time summary sidecars can now serve `list_sessions_from_store()` without loading full transcripts, reducing round-trips from N to 1 for N sessions (#847)
- **Import local sessions to store**: Added `import_session_to_store()` for replaying a local on-disk session into any `SessionStore` adapter, enabling migration from local storage to remote stores (#858)
- **Thinking display control**: Added `display` field to `ThinkingConfig` types, forwarded as `--thinking-display` to the CLI. This lets callers override Opus 4.7's default `"omitted"` behavior and receive summarized thinking text (#830)
- **Server tool use and advisor result blocks**: Added `ServerToolUseBlock` and `AdvisorToolResultBlock` content block types, surfacing server-executed tool calls (e.g., `advisor`, `web_search`) and their results that were previously silently dropped (#836)

### Bug Fixes

- **Missing content blocks**: Fixed `server_tool_use` and `advisor_tool_result` content blocks being silently dropped by the message parser, which caused messages carrying only server-side tool calls to arrive as empty `AssistantMessage(content=[])` (#836)

### Documentation

- Fixed misleading `permission_mode` docstrings: `dontAsk` now correctly described as denying unapproved tools (was inverted), and `auto` clarified as using a model classifier (#863)

### Internal/Other Changes

- Dropped `--debug-to-stderr` detection from the transport layer in preparation for CLI flag removal; stderr piping now depends solely on whether a `stderr` callback is registered (#860)
- Added bounded retry on session mirror append and UUID idempotency documentation (#857)
- Updated bundled Claude CLI to version 2.1.118

## 0.1.64

### New Features

- **SessionStore adapter**: Full SessionStore support at parity with the TypeScript SDK. Includes a `SessionStore` protocol with 5 methods (`append`, `load`, `list_sessions`, `delete`, `list_subkeys`), `InMemorySessionStore` reference implementation, transcript mirroring via `--session-mirror`, session resume from store, and 9 new async store-backed helper functions (`list_sessions_from_store`, `get_session_messages_from_store`, `fork_session_via_store`, etc.). Also adds a 13-contract conformance test harness at `claude_agent_sdk.testing.run_session_store_conformance` for third-party adapter authors (#837)
- **Reference SessionStore adapters**: Three copy-in reference `SessionStore` adapters under `examples/session_stores/` — S3 (JSONL part files, mirrors the TS S3 reference), Redis (RPUSH/LRANGE lists + zset index), and Postgres (`asyncpg` + jsonb rows). Not shipped in the wheel; users copy the file they need into their project (#842)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.116

## 0.1.63

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.114

## 0.1.62

### New Features

- **Top-level `skills` option**: Added `skills` parameter to `ClaudeAgentOptions` for enabling skills on the main session without manually configuring `allowed_tools` and `setting_sources`. Supports `"all"` for every discovered skill, a list of named skills, or `[]` to suppress all skills (#804)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.113

## 0.1.61

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.112

## 0.1.60

### New Features

- **Subagent transcript helpers**: Added `list_subagents()` and `get_subagent_messages()` session helpers for reading subagent transcripts, enabling inspection of subagent message chains spawned during a session (#825)
- **Distributed tracing**: Propagate W3C trace context (`TRACEPARENT`/`TRACESTATE`) to the CLI subprocess when an OpenTelemetry span is active, connecting SDK and CLI traces end-to-end. Install with `pip install claude-agent-sdk[otel]` for optional OpenTelemetry support (#821)
- **Cascading session deletion**: `delete_session()` now removes the sibling subagent transcript directory alongside the session file, matching TypeScript SDK behavior (#805)

### Bug Fixes

- **Empty setting sources**: Fixed `setting_sources=[]` being silently dropped (treated as falsy), which caused the CLI to load default settings instead of disabling all filesystem settings. An empty list now correctly passes `--setting-sources=` to disable all sources (#822)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.111

## 0.1.59

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.105

## 0.1.58

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.97

## 0.1.57

### New Features

- **Cross-user prompt caching**: Added `exclude_dynamic_sections` option to `SystemPromptPreset`, enabling cross-user prompt cache hits by moving per-user dynamic sections (working directory, memory, git status) out of the system prompt (#797)
- **Auto permission mode**: Added `"auto"` to the `PermissionMode` type, bringing parity with the TypeScript SDK and CLI v2.1.90+ (#785)

### Bug Fixes

- **Thinking configuration**: Fixed `thinking={"type": "adaptive"}` incorrectly mapping to `--max-thinking-tokens 32000` instead of `--thinking adaptive`. The `disabled` type similarly now uses `--thinking disabled` instead of `--max-thinking-tokens 0`, matching the TypeScript SDK behavior (#796)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.96

## 0.1.56

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.92

## 0.1.55

### Bug Fixes

- **MCP large tool results**: Forward `maxResultSizeChars` from `ToolAnnotations` via `_meta` to bypass Zod annotation stripping in the CLI, fixing silent truncation of large MCP tool results (>50K chars) (#756)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.91

## 0.1.53

### Bug Fixes

- **Setting sources flag**: Fixed `--setting-sources` being passed as an empty string when not provided, which caused the CLI to misparse subsequent flags (#778)
- **String prompt deadlock**: Fixed deadlock when using `query()` with a string prompt and hooks/MCP servers that trigger many tool calls, by spawning `wait_for_result_and_end_input()` as a background task (#780)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.88

## 0.1.52

### New Features

- **Context usage**: Added `get_context_usage()` method to `ClaudeSDKClient` for querying context window usage by category (#764)
- **Annotated parameter descriptions**: The `@tool` decorator and `create_sdk_mcp_server` now support `typing.Annotated` for per-parameter descriptions in JSON Schema (#762)
- **ToolPermissionContext fields**: Exposed `tool_use_id` and `agent_id` in `ToolPermissionContext` for distinguishing parallel permission requests (#754)
- **Session ID option**: Added `session_id` option to `ClaudeAgentOptions` for specifying custom session IDs (#750)

### Bug Fixes

- **String prompt in connect()**: Fixed `connect(prompt="...")` silently dropping the string prompt, causing `receive_messages()` to hang indefinitely (#769)
- **Cancel request handling**: Implemented `control_cancel_request` handling so in-flight hook callbacks are properly cancelled when the CLI abandons them (#751)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.87
- Increased CI timeout for example tests and reduced sleep duration in error handling example (#760)

## 0.1.51

### New Features

- **Session management**: Added `fork_session()`, `delete_session()`, and offset-based pagination for session listing (#744)
- **Task budget**: Added `task_budget` option for token budget management (#747)
- **SystemPromptFile**: Added support for `--system-prompt-file` CLI flag via `SystemPromptFile` (#591)
- **AgentDefinition fields**: Added `disallowedTools`, `maxTurns`, and `initialPrompt` to `AgentDefinition` (#759)
- **Preserved fields**: Preserve dropped fields on `AssistantMessage` and `ResultMessage` for forward compatibility (#718)

### Bug Fixes

- **Python 3.10 compatibility**: Use `typing_extensions.TypedDict` on Python 3.10 for `NotRequired` support (#761)
- **ResultMessage errors field**: Added missing `errors` field to `ResultMessage` (#749)
- **Async generator cleanup**: Resolved cross-task cancel scope `RuntimeError` on async generator cleanup (#746)
- **MCP tool input_schema**: Convert `TypedDict` input_schema to proper JSON Schema in SDK MCP tools (#736)
- **initialize_timeout**: Pass `initialize_timeout` from env var in `query()` (#743)
- **Async event loop blocking**: Defer CLI discovery to `connect()` to avoid blocking async event loops (#722)
- **Permission mode**: Added missing `dontAsk` permission mode to types (#719)
- **Environment filtering**: Filter `CLAUDECODE` env var from subprocess environment (#732)
- **Process cleanup**: Added `SIGKILL` fallback when `SIGTERM` handler blocks in `close()` (#729)
- **Duplicate warning**: Removed duplicate version warning and included CLI path (#720)
- **MCP resource types**: Handle `resource_link` and embedded resource content types in SDK MCP tools (#725)
- **Stdin timeout**: Removed stdin timeout for hooks and SDK MCP servers (#731)
- **Stdout parsing**: Skip non-JSON lines on CLI stdout to prevent buffer corruption (#723)
- **MCP error propagation**: Propagate `is_error` flag from SDK MCP tool results (#717)
- **Install script**: Retry `install.sh` fetch on 429 with pipefail + jitter (#708)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.85

## 0.1.50

### New Features

- **Session info**: Added `tag` and `created_at` fields to `SDKSessionInfo` and new `get_session_info()` function for retrieving session metadata (#667)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.81
- Hardened PyPI publish workflow against partial-upload failures (#700)
- Added daily PyPI storage quota monitoring (#705)

## 0.1.49

### New Features

- **AgentDefinition**: Added `skills`, `memory`, and `mcpServers` fields (#684)
- **AssistantMessage usage**: Preserve per-turn `usage` on `AssistantMessage` (#685)
- **Session tagging**: Added `tag_session()` with Unicode sanitization (#670)
- **Session renaming**: Added `rename_session()` (#668)
- **RateLimitEvent**: Added typed `RateLimitEvent` message (#648)

### Bug Fixes

- **CLAUDE_CODE_ENTRYPOINT**: Use default-if-absent semantics to match TS SDK (#686)
- **Fine-grained tool streaming**: Reverted the env-var workaround from 0.1.48; partial-message delivery is now handled upstream (#671)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.77
- Added macOS x86_64 wheel to the published matrix (#661)
- Upload wheel-check artifacts in CI (#662)
- Docs: clarified `allowed_tools` as a permission allowlist (#649)

## 0.1.48

### Bug Fixes

- **Fine-grained tool streaming**: Fixed `include_partial_messages=True` not delivering `input_json_delta` events by enabling the `CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING` environment variable in the subprocess. This regression affected versions 0.1.36 through 0.1.47 for users without the server-side feature flag (#644)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.71

## 0.1.47

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.70

## 0.1.46

### New Features

- **Session history functions**: Added `list_sessions()` and `get_session_messages()` top-level functions for retrieving past session data (#622)
- **MCP control methods**: Added `add_mcp_server()`, `remove_mcp_server()`, and typed `McpServerStatus` for runtime MCP server management (#620)
- **Typed task messages**: Added `TaskStarted`, `TaskProgress`, and `TaskNotification` message subclasses for better type safety when handling task-related events (#621)
- **ResultMessage stop_reason**: Added `stop_reason` field to `ResultMessage` for inspecting why a conversation turn ended (#619)
- **Hook input enhancements**: Added `agent_id` and `agent_type` fields to tool-lifecycle hook inputs (`PreToolUseHookInput`, `PostToolUseHookInput`, `PostToolUseFailureHookInput`) (#628)

### Bug Fixes

- **String prompt MCP initialization**: Fixed an issue where passing a string prompt would close stdin before MCP server initialization completed, causing MCP servers to fail to register (#630)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.69

## 0.1.45

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.63

## 0.1.44

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.59

## 0.1.43

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.56

## 0.1.42

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.55

## 0.1.41

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.52

## 0.1.40

### Bug Fixes

- **Unknown message type handling**: Fixed an issue where unrecognized CLI message types (e.g., `rate_limit_event`) would crash the session by raising `MessageParseError`. Unknown message types are now silently skipped, making the SDK forward-compatible with future CLI message types (#598)

### Internal/Other Changes

- Updated bundled Claude CLI to version 2.1.51

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
