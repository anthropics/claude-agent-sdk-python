# Changelog

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

