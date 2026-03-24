---
updated: 2026-03-22
type: context
topic: learning-resources
---

# Claude Agent SDK — Learning Resources

> Curated 2026-03-22 | 18 resources across 4 categories

## Category 1: Official Documentation (Primary Source)

| # | Resource | URL | Relevance |
|---|----------|-----|-----------|
| D1 | **Agent SDK Overview** | [platform.claude.com/docs/en/agent-sdk/overview](https://platform.claude.com/docs/en/agent-sdk/overview) | Architecture, concepts, capabilities overview |
| D2 | **Python SDK Reference** | [platform.claude.com/docs/en/agent-sdk/python](https://platform.claude.com/docs/en/agent-sdk/python) | Complete API reference: functions, types, classes |
| D3 | **Quickstart Guide** | [platform.claude.com/docs/en/agent-sdk/quickstart](https://platform.claude.com/docs/en/agent-sdk/quickstart) | Step-by-step getting started |
| D4 | **GitHub Repository** | [github.com/anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | Source code, issues, examples |

---

## Category 2: YouTube Videos (Sorted by Relevance)

### Tier 1: Must Watch (Directly about Claude Agent SDK)

| # | Title | Channel | Duration | Views | Date | Link |
|---|-------|---------|----------|-------|------|------|
| V1 | **Claude Agent SDK [Full Workshop]** — Thariq Shihipar, Anthropic | AI Engineer | 1h52m | 90K | 2026-01 | [Watch](https://www.youtube.com/watch?v=TqC1qOfiVcQ) |
| V2 | **Build & deploy agents with the Claude Agent SDK** | Matt Palmer (Replit) | 30m+ | - | 2026-02 | [Watch](https://www.youtube.com/watch?v=jNpH_hOFvg4) |
| V3 | **Claude Agent SDK — complete walkthrough in 12 mins!** | AI Bites | 12m | - | 2025-11 | [Watch](https://www.youtube.com/watch?v=u1uyXXl_6N8) |

**V1 highlights (most comprehensive):**
- Thariq Shihipar (Anthropic) builds agent harness from scratch
- Covers: Agent Loop (Context→Thought→Action→Observation), Bash tool, Context Engineering
- Live coding: Research Agent demo, Hooks for determinism
- Key insight: "SDK is Claude Code in library form"
- Timestamps: 0:00 Introduction, 25:20 Agent Loop, 42:00 Bash Tool, 58:15 Context Engineering, 1:35:10 Hooks

**V2 highlights:**
- Agent loop architecture: prompt, tools, observe, repeat
- Multi-agent orchestrator patterns
- Tools vs MCP servers vs Skills comparison (5:30)
- Deploying scheduled automations on Replit
- Live build: Todoist organizer agent

**V3 highlights:**
- Quick practical walkthrough with code examples
- Links to official blog + SDK docs

### Tier 2: Highly Relevant (Skills, Hooks, Patterns)

| # | Title | Channel | Duration | Views | Date | Link |
|---|-------|---------|----------|-------|------|------|
| V4 | **Don't Build Agents, Build Skills Instead** — Barry Zhang & Mahesh Murag, Anthropic | AI Engineer | 16m | 933K | 2025-12 | [Watch](https://www.youtube.com/watch?v=CEvIs9y1uog) |
| V5 | **Claude Agent Skills Explained** | Anthropic (official) | 3m | 161K | 2025-11 | [Watch](https://www.youtube.com/watch?v=fOxC44g8vig) |
| V6 | **I finally CRACKED Claude Agent Skills** | IndyDevDan | 27m | 75K | 2025-10 | [Watch](https://www.youtube.com/watch?v=kFpLzCVLA20) |
| V7 | **How Claude Code Hooks Save Me HOURS Daily** | Better Stack | 3m | 29K | 2025-07 | [Watch](https://www.youtube.com/watch?v=Q4gsvJvRjCU) |
| V8 | **Claude Code Skills Just Got a MASSIVE Upgrade** | Chase AI | 12m | 101K | 2026-03 | [Watch](https://www.youtube.com/watch?v=UxfeF4bSBYI) |

**V4 highlights (most viewed — 933K):**
- Anthropic engineers explain Skills as "portable, composable expertise"
- Skills vs MCP vs Subagents: when to use each
- Vision: agents writing their own Skills from experience

**V6 highlights:**
- Skills vs MCP servers vs sub-agents vs slash commands decision framework
- Live build: Git worktree manager skill
- Key insight: "Prompt is the fundamental unit — Skills are compositional, not replacements"

**V7 highlights:**
- 5 hook types: PreToolUse, PostToolUse, Notification, Stop, SubagentStop
- Practical examples: blocking npm for bun, audit logs, linting, audio notifications

### Tier 3: General Context (Claude Code ecosystem)

| # | Title | Channel | Duration | Views | Date | Link |
|---|-------|---------|----------|-------|------|------|
| V9 | **Claude Code Mastery Guide** | Vaibhav Sisinty | 14m | 361K | 2026-01 | [Watch](https://www.youtube.com/watch?v=_0xa6RVqTC8) |

---

## Category 3: Written Tutorials & Blog Posts

| # | Title | Source | Relevance |
|---|-------|--------|-----------|
| B1 | **The Complete Guide to Building Agents with the Claude Agent SDK** | [Nader's Substack](https://nader.substack.com/p/the-complete-guide-to-building-agents) | End-to-end tutorial with practical examples |
| B2 | **Claude Agent SDK Tutorial: Create Agents Using Claude Sonnet** | [DataCamp](https://www.datacamp.com/tutorial/how-to-use-claude-agent-sdk) | Structured tutorial with code exercises |
| B3 | **Getting Started with the Claude Agent SDK** | [KDnuggets](https://www.kdnuggets.com/getting-started-with-the-claude-agent-sdk) | Beginner-friendly introduction |
| B4 | **Getting started with Anthropic Claude Agent SDK — Python** | [Medium/@aiablog](https://medium.com/@aiablog/getting-started-with-anthropic-claude-agent-sdk-python-826a2216381d) | Step-by-step Python setup guide |
| B5 | **A practical guide to the Python Claude Code SDK** | [eesel.ai Blog](https://www.eesel.ai/blog/python-claude-code-sdk) | Practical patterns and best practices |
| B6 | **Build Production AI Agents with Claude Agent SDK** | [Let's Data Science](https://letsdatascience.com/blog/claude-agent-sdk-tutorial) | Production deployment focus |

---

## Category 4: Related GitHub Resources

| # | Resource | URL | Relevance |
|---|----------|-----|-----------|
| G1 | **Official SDK Python repo** | [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | Source code, examples in repo |
| G2 | **Multi-Agent Observability Hooks** | [disler/claude-code-hooks-multi-agent-observability](https://github.com/disler/claude-code-hooks-multi-agent-observability) | Referenced in IndyDevDan's video (V6) |

---

## Recommended Learning Path

| Order | Resource | Why | Time |
|-------|----------|-----|------|
| 1 | D3 Quickstart | Official getting started | 15 min |
| 2 | V3 AI Bites walkthrough | Quick practical overview | 12 min |
| 3 | D2 Python SDK Reference | API surface understanding | 30 min |
| 4 | V1 Full Workshop (Thariq) | Deep architecture + live coding | 1h52m |
| 5 | V4 Skills talk (Anthropic) | Skills philosophy + patterns | 16 min |
| 6 | V7 Hooks tutorial | Hook system practical examples | 3 min |
| 7 | B1 Complete Guide (Nader) | End-to-end written reference | 30 min |
| 8 | V2 Build & Deploy (Replit) | Multi-agent + deployment | 30 min |

**Total: ~4.5 hours for comprehensive coverage**

---

## Key Takeaways from Resources

1. **SDK = Claude Code as a library** — Same tools, agent loop, and context management
2. **Agent Loop**: Context → Thought → Action → Observation → repeat
3. **Bash is the most powerful tool** — General computer use > bespoke tools
4. **Context Engineering > Prompt Engineering** — File system as state manager
5. **Skills = portable expertise** — Composable, not replacements for prompts
6. **Hooks = deterministic control** — Override agent behavior at specific points
7. **Tools vs MCP vs Skills**: Tools for simple functions, MCP for external services, Skills for packaged expertise
