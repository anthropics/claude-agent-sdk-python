---
date: 2026-03-21
type: task-worklog
task: claudeagentsdk-qw0
title: "P2: Nghien cuu use cases & chien luoc su dung hieu qua"
status: open
detailed_at: 2026-03-21
detail_score: ready-for-dev
tags: [research, use-cases, strategy, p2, examples, context7]
---

# Nghien cuu use cases & chien luoc su dung hieu qua — Detailed Design

## 1. Objective
Analyze all 18 example files and Context7 demo applications to create a use case guide with 7+ categories and a query() vs ClaudeSDKClient decision matrix, enabling developers to choose the right approach for any scenario.

## 2. Scope
**In-scope:**
- Reading and categorizing all 18 files in the `examples/` directory
- Fetching Context7 `/anthropics/claude-agent-sdk-demos` (345 snippets) for additional patterns
- Creating 7+ use case categories with descriptions
- Building a query() vs ClaudeSDKClient decision matrix with 5+ scenario rows
- Documenting common patterns and anti-patterns with code snippet references
- Identifying which SDK features each example demonstrates

**Out-of-scope:**
- Running or testing any example code
- Creating new example files
- Performance benchmarking of different approaches
- Comparing with other AI SDK libraries
- Modifying existing examples

## 3. Input / Output
**Input:**
- `examples/` directory (18 files):
  - `quick_start.py` -- basic usage
  - `streaming_mode.py`, `streaming_mode_ipython.py`, `streaming_mode_trio.py` -- streaming variants
  - `hooks.py` -- hook system usage
  - `mcp_calculator.py` -- MCP tools in-process
  - `agents.py`, `filesystem_agents.py` -- agent definitions
  - `tool_permission_callback.py`, `tools_option.py` -- tool control
  - `system_prompt.py` -- system prompt customization
  - `setting_sources.py` -- settings management
  - `max_budget_usd.py` -- budget control
  - `include_partial_messages.py` -- partial message handling
  - `stderr_callback_example.py` -- error callback
  - `plugin_example.py` + `plugins/` -- plugin system
  - (remaining files to be confirmed during Step 1)
- Context7 MCP: `/anthropics/claude-agent-sdk-demos` (345 snippets)
- Prior task outputs: `self-explores/context/docs-summary.md` (from 3ma), `self-explores/context/code-architecture.md` (from d0g)

**Output:**
- `self-explores/context/use-case-guide.md` -- Comprehensive guide containing:
  1. Use case categories (7+ categories)
  2. Per-category analysis (features used, entry point, complexity)
  3. query() vs ClaudeSDKClient decision matrix (5+ rows)
  4. Common patterns with example file references
  5. Anti-patterns and gotchas
  6. Advanced patterns from Context7 demos

## 4. Dependencies
- **Task dependencies:**
  - `claudeagentsdk-3ma` (P1: docs overview) -- needs conceptual understanding of SDK features
  - `claudeagentsdk-d0g` (P1: code architecture) -- needs understanding of internal flows to explain why query() vs ClaudeSDKClient matters
- **Tool dependencies:**
  - Read tool -- for reading all 18 example files
  - Glob tool -- for discovering all files in examples/
  - Context7 MCP (`mcp__context7__query-docs`) -- for demo app patterns
  - Write tool -- for creating output file
- **Directory:** `self-explores/context/` should already exist from prior tasks

## 5. Flow

### Step 1: Read all 18 example files (~20 min)
Use Glob to discover all files in `examples/`:
```
Glob pattern: examples/**/*.py
```

Read each file completely. For each example, document:
- **File name and purpose** (1 sentence)
- **Entry point used:** query() or ClaudeSDKClient or both
- **SDK features demonstrated:** (hooks, MCP, agents, streaming, permissions, budget, etc.)
- **Complexity level:** simple (< 30 lines), moderate (30-80 lines), advanced (80+ lines)
- **Key code pattern:** The main technique the example teaches

Read in batches of 3-4 files to maintain context. Prioritize order:
1. `quick_start.py` first (baseline understanding)
2. Core API examples: `streaming_mode.py`, `system_prompt.py`, `max_budget_usd.py`
3. Feature examples: `hooks.py`, `mcp_calculator.py`, `agents.py`, `tool_permission_callback.py`
4. Advanced examples: `plugins/`, `filesystem_agents.py`, `include_partial_messages.py`
5. Remaining files

**Verify:** All files found by Glob have been read and documented. Count matches expected 18.

### Step 1b: Fetch Context7 demo applications (~5 min)
Use `mcp__context7__query-docs` with library ID `/anthropics/claude-agent-sdk-demos`:
- Query 1: `"demo applications use cases hooks MCP tools agents"`
- Query 2: `"real world examples patterns production"` (if first query insufficient)

Extract:
- Demo app names and descriptions
- Patterns not present in local `examples/` directory
- Production-grade patterns (error handling, retry, logging)
- Multi-agent orchestration examples

**Verify:** At least 3 additional patterns or use cases found beyond local examples.

### Step 2: Categorize into 7+ use case categories (~10 min)
Based on analysis from Step 1 and 1b, create categories. Expected categories:

1. **One-shot Query** -- Batch processing, CI/CD integration, scripts
   - Examples: `quick_start.py`, files using `query()`
   - Pattern: Fire-and-forget, no state needed

2. **Interactive Conversation** -- Chat UIs, REPLs, debugging sessions
   - Examples: files using `ClaudeSDKClient` with multi-turn
   - Pattern: Session lifecycle, follow-ups

3. **Custom Tools via MCP** -- Extending Claude with domain-specific tools
   - Examples: `mcp_calculator.py`
   - Pattern: `@tool` decorator, in-process execution

4. **Hook-based Control** -- Security gates, logging, content filtering
   - Examples: `hooks.py`
   - Pattern: PreToolUse/PostToolUse callbacks, allow/deny decisions

5. **Agent Orchestration** -- Multi-agent systems, delegated tasks
   - Examples: `agents.py`, `filesystem_agents.py`
   - Pattern: Agent definitions, subagent configuration

6. **Permission & Safety Management** -- Access control, tool restrictions
   - Examples: `tool_permission_callback.py`, `tools_option.py`
   - Pattern: can_use_tool callbacks, permission modes

7. **Streaming & Real-time** -- Live UIs, progress tracking, partial results
   - Examples: `streaming_mode.py`, `include_partial_messages.py`
   - Pattern: Async iteration, partial message handling

Additional categories if examples support them:
8. **Budget & Cost Control** -- Usage limits, cost tracking
9. **Plugin System** -- Extensible plugin architecture
10. **Settings & Configuration** -- Dynamic settings management

For each category, document: description, example files, entry point recommendation, complexity, key SDK features used.

**Verify:** At least 7 categories defined, each mapped to 1+ example files. No example file is uncategorized.

### Step 3: Create query() vs ClaudeSDKClient decision matrix (~10 min)
Build a table with columns: Scenario, Recommended Entry Point, Reason, Example File

Minimum 5 rows:

| Scenario | Recommended | Reason | Example |
|----------|-------------|--------|---------|
| Single prompt, no follow-up | `query()` | Stateless, simpler, auto-cleanup | `quick_start.py` |
| Multi-turn conversation | `ClaudeSDKClient` | Maintains session state, supports follow-ups | (client examples) |
| CI/CD pipeline integration | `query()` | No session needed, fire-and-forget | -- |
| Custom MCP tools | Either (prefer Client for complex) | Both support MCP; Client allows dynamic tool changes | `mcp_calculator.py` |
| Hook-based security gates | `ClaudeSDKClient` | Hooks need session context for stateful decisions | `hooks.py` |
| Real-time streaming UI | `ClaudeSDKClient` | Partial messages, interrupt support | `streaming_mode.py` |
| Agent orchestration | `ClaudeSDKClient` | Complex lifecycle, multi-agent state | `agents.py` |
| Budget-limited batch job | `query()` | Simple budget cap, no state needed | `max_budget_usd.py` |

For each row, also note:
- Required SDK features
- Complexity (simple/moderate/advanced)
- Common mistakes to avoid

**Verify:** Matrix has 5+ rows. Each row has a clear recommendation with reasoning. At least 3 rows recommend query() and 3 recommend ClaudeSDKClient.

### Step 4: Document patterns, anti-patterns, and snippets (~5 min)
**Patterns (do this):**
- Proper async context manager usage for ClaudeSDKClient
- Error handling with ClaudeSDKError hierarchy
- Streaming message iteration pattern
- Hook callback structure (async function signature)
- MCP tool definition with @tool decorator

**Anti-patterns (avoid this):**
- Not awaiting cleanup (forgetting `async with` or `__aexit__`)
- Blocking in async callbacks (using sync I/O in hook functions)
- Ignoring partial messages in streaming (missing state updates)
- Hardcoding CLI paths instead of using SDK's auto-discovery
- Not handling CLINotFoundError on startup

For each pattern/anti-pattern, reference the specific example file that demonstrates (or would demonstrate) it.

**Verify:** At least 3 patterns and 3 anti-patterns documented, each with example file reference.

## 6. Edge Cases & Error Handling
| Case | Trigger | Expected | Recovery |
|------|---------|----------|----------|
| Some examples not runnable | Example requires CLI binary or API key not available | Cannot verify behavior | Still categorize based on code reading; note "not verified at runtime" |
| Demo repo has patterns not in local examples | Context7 demos cover advanced scenarios | New categories needed | Include as "Advanced Patterns (from demos)" section; clearly mark source |
| Example file count differs from 18 | Glob finds more or fewer files | Category mapping may be incomplete | Use actual Glob results; update count in output |
| Example uses deprecated API | Example uses old ClaudeCodeOptions name | Misleading categorization | Note the deprecation; document the current equivalent |
| Prior task outputs missing | Tasks 3ma/d0g not yet completed | Missing context for decision matrix reasoning | Proceed with current understanding from CLAUDE.md architecture notes; update guide later if needed |
| Context7 demos unavailable | MCP server or library ID fails | No additional patterns from demos | Focus exclusively on local examples; note "demo patterns not available" |

## 7. Acceptance Criteria
- **Happy 1:** Given all 18 examples and Context7 demos analyzed, When guide created, Then it has 7+ categories with each category mapped to at least 1 example file and containing description, entry point recommendation, and complexity level
- **Happy 2:** Given decision matrix created, When a developer reads it, Then they can determine whether to use query() or ClaudeSDKClient for any of 5+ common scenarios, with clear reasoning for each recommendation
- **Happy 3:** Given patterns and anti-patterns documented, When developer reviews them, Then each pattern has a concrete example file reference and each anti-pattern has an explanation of what goes wrong
- **Negative:** Given some examples are not runnable without CLI, When categorizing, Then examples are still categorized based on code reading with note "behavior not runtime-verified"

## 8. Technical Notes
- The `examples/` directory may contain subdirectories (e.g., `plugins/`) -- use recursive Glob
- SDK renamed from "Claude Code SDK" to "Claude Agent SDK" -- some examples may use old naming
- `streaming_mode_trio.py` uses Trio instead of asyncio -- note this as an alternative async runtime example
- `streaming_mode_ipython.py` is for IPython/Jupyter context -- note the different execution environment
- Context7 demo library: `/anthropics/claude-agent-sdk-demos` (345 snippets)
- Decision matrix should consider both technical constraints and developer experience (DX)
- Anti-patterns should reference the error types from `_errors.py` where applicable

## 9. Risks
- **Risk:** 50 minutes may be tight for reading 18 files + demos + creating comprehensive guide. **Mitigation:** Batch-read examples in groups; spend no more than 2 minutes per simple example. Allocate 20 min for reading, 30 min for analysis and writing.
- **Risk:** Examples may be trivial and not demonstrate real-world complexity. **Mitigation:** Context7 demos supplement with production patterns; also extrapolate realistic scenarios from the API surface.
- **Risk:** Decision matrix may be too simplistic for edge cases. **Mitigation:** Include a "Notes" column for nuance; add a section on "when the answer is not clear-cut."

## Worklog

### [00:10] Steps 1-4 — Read examples + Context7 demos + categorize + matrix
**Kết quả:**
- Read 16 .py files (not 18 — 2 were plugin config files, not .py)
- 7 use query(), 9 use ClaudeSDKClient, none use both
- Context7 demos: 4 TypeScript patterns (WebSocket chat, file validation hook, email MCP server, AI classification)

**Categories identified: 10**
1. One-Shot Query (7 examples)
2. Interactive Conversation (3 examples)
3. Custom Tools via MCP (1 example + demos)
4. Hook-Based Control (1 example, 5 patterns)
5. Permission & Safety (1 example)
6. Agent Orchestration (2 examples)
7. Streaming & Real-Time (2 examples + demos)
8. Budget & Cost Control (1 example)
9. Configuration & Settings (3 examples)
10. Plugin System (1 example)

**Decision matrix: 12 rows** with scenario, recommendation, reason, example file

**Patterns: 5 common + 5 anti-patterns** with code snippets

**Files tạo:** `self-explores/context/use-case-guide.md`

### [00:15] AC Verification
- [x] 10 categories (target: 7+) — PASS
- [x] Decision matrix 12 rows (target: 5+) — PASS
- [x] query() vs ClaudeSDKClient recommendations clear — PASS
- [x] 5 patterns + 5 anti-patterns with example refs — PASS
- [x] Context7 demo patterns included — PASS
