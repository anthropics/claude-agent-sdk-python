---
date: 2026-03-21
type: task-worklog
task: claudeagentsdk-1ig
title: "Feynman learning"
status: open
detailed_at: 2026-03-21
detail_score: ready-for-dev
tags: [learning, feynman, explanation, mental-model, phase-3]
---

# Feynman Learning — Detailed Design

## 1. Objective

Write Feynman-method learning content (~2000 words) that enables a Python developer who knows async/await but has never used claude-agent-sdk to understand its core concepts through simple analogies, gap identification, and a mental model diagram.

## 2. Scope

**In-scope:**
- Core concepts ONLY: `query()`, `ClaudeSDKClient`, hooks, MCP in-process tools
- 4 clear analogies mapped to these concepts
- Identified knowledge gaps and their resolutions
- Mental model diagram (Mermaid or ASCII)
- Prerequisite statement for target audience
- Hard limit: ~2000 words

**Out-of-scope:**
- Advanced topics: plugins, agent orchestration, session persistence, sandbox mode
- API reference or exhaustive parameter documentation
- Installation/setup instructions (covered in cheatsheet)
- Comparison with other SDKs or tools
- Code examples longer than 10 lines (this is conceptual, not a tutorial)

## 3. Input / Output

**Input:**
- `self-explores/context/claude-agent-sdk-overview.md` (from task aca — executive summary)
- `self-explores/context/claude-agent-sdk-cheatsheet.md` (from task aca — quick reference)
- `self-explores/context/code-architecture.md` (from task d0g — for gap-filling)
- Source code (for Step 3 gap-filling only): `src/claude_agent_sdk/_internal/query.py`, `src/claude_agent_sdk/_internal/transport/subprocess_cli.py`

**Output:**
- `self-explores/learnings/2026-03-21-claude-agent-sdk-feynman.md` — single Markdown file, ~2000 words

## 4. Dependencies

- `claudeagentsdk-aca` (executive summary + cheatsheet) — MUST be completed first; provides the synthesized understanding to explain simply
- Source code access for Step 3 (gap-filling) — no task dependency, just file access

## 5. Flow

### Step 1: Feynman Step 1 — Explain Simply with Analogies (~15 min)

Write the first draft using 4 core analogies. Each analogy must:
- Be relatable to a Python developer (use programming metaphors where helpful)
- Map cleanly to the actual SDK behavior (no misleading simplifications)
- Be introduced with "Think of X as Y" pattern

**Analogy 1: query() = Sending a Letter**
- You write a letter (prompt), put it in an envelope (options), drop it in the mailbox (query function)
- The postal service (CLI subprocess) delivers it, processes it, and sends back a reply
- You get the reply as a stream of pages (AsyncIterator[Message]) — you read them as they arrive
- When the last page arrives, the conversation is over. No way to reply without sending a new letter
- Key insight: fire-and-forget, stateless, automatic cleanup

**Analogy 2: ClaudeSDKClient = Phone Call**
- You pick up the phone (async with ClaudeSDKClient)
- The connection is established (initialize handshake)
- You talk (query), listen (receive_response), talk again (follow-up) — it's a conversation
- You can interrupt mid-sentence (client.interrupt())
- When you hang up (exit async with), the line is closed and resources freed
- Key insight: stateful, bidirectional, you control the lifecycle

**Analogy 3: Transport = The Pipe Under the Floor**
- Between your Python code and the CLI, there's a pipe (SubprocessCLITransport)
- You never touch the pipe directly — you talk to query() or ClaudeSDKClient, and they handle the plumbing
- The pipe carries JSON messages in both directions (stdin = you to CLI, stdout = CLI to you)
- The pipe is always streaming — even query() uses stream mode internally
- Key insight: abstraction layer, JSON protocol, you don't need to know the pipe exists

**Analogy 4: Hooks = Checkpoint Guards**
- Imagine the CLI is walking down a corridor (executing a task)
- At certain doors (hook points: PreToolUse, PostToolUse, Stop), there's a guard (your hook function)
- The guard checks the situation and decides: "go ahead" (approve), "stop" (block), or "take a detour" (modify)
- Guards are async — they can take time to decide without blocking the whole building
- You install guards by registering hook matchers that specify which doors they watch
- Key insight: intercept points for safety and control, non-blocking, pattern-matched

**Bonus Analogy: MCP Tools = Your Own Toolbox**
- The CLI comes with built-in tools (Bash, Read, Write, etc.)
- MCP in-process tools let you add your own tools to the toolbox using `@tool` decorator
- When Claude decides to use your tool, the request stays inside your Python process (no subprocess)
- Think of it as a plugin system where you teach Claude new skills
- Key insight: extensibility, in-process execution, decorator-based

Write each analogy as 2-3 paragraphs. Include a brief "What this maps to in code" callout after each.

**Verify:** Each analogy is understandable without SDK knowledge. Each maps to a real SDK concept. No analogy is misleading about actual behavior.

### Step 2: Feynman Step 2 — Identify Knowledge Gaps (~10 min)

After writing the simple explanations, identify what's still confusing or hand-wavy. List the gaps:

**Gap 1: Control Protocol**
- "JSON messages over stdin/stdout" — but HOW? What's the message format? How are requests matched to responses?
- The `request_id` matching mechanism is non-obvious
- The initialize handshake is a specific protocol, not just "start talking"

**Gap 2: Streaming Model**
- "Always streaming internally" — what does this mean for query() which returns one response?
- How does the SDK buffer/parse partial JSON from a stream?
- What happens when the CLI sends multiple JSON objects on a single stdout line?

**Gap 3: Hook Callback Mechanism**
- How does the CLI "pause" and wait for the hook response?
- What's the threading model? Does the hook block the message stream?
- How are async_ and continue_ field name conversions handled?

**Gap 4: anyio Async Model**
- Why anyio instead of plain asyncio?
- What are task groups and how does the SDK use them?
- Does this mean the SDK works with trio too?

Write each gap as a question the reader would naturally ask after reading the analogies.

**Verify:** At least 4 gaps identified. Each is a genuine confusion point, not a trivial detail.

### Step 3: Feynman Step 3 — Go Back to Source to Fix Gaps (~10 min)

For each gap, read the relevant source code and write a clear resolution:

**Resolving Gap 1:** Read `_internal/query.py` — describe the control protocol:
- Each request has a `request_id` (UUID)
- Responses include the matching `request_id`
- The `Query` class maintains a pending requests dict and resolves futures when responses arrive

**Resolving Gap 2:** Read `_internal/transport/subprocess_cli.py` — describe streaming:
- Transport reads stdout line by line
- Each line is a complete JSON object (newline-delimited JSON, aka NDJSON)
- `MessageParser` converts raw dicts into typed dataclasses
- query() collects the stream internally; ClaudeSDKClient exposes it

**Resolving Gap 3:** Read `_internal/query.py` hook handling:
- CLI sends a hook callback request (just another JSON message with a request_id)
- Query receives it, calls the registered Python async function
- The Python function returns a decision
- Query sends the decision back as a response
- CLI was waiting for this response before continuing — it's synchronous from CLI's perspective

**Resolving Gap 4:** Explain anyio briefly:
- anyio is an abstraction over asyncio and trio
- The SDK uses `anyio.create_task_group()` for concurrent operations
- In practice, most users will use asyncio — trio support is a bonus
- Task groups ensure all spawned tasks are properly cleaned up

**Verify:** Each gap has a concrete resolution. Resolutions are accurate (cross-referenced with source code). Language remains simple.

### Step 4: Feynman Step 4 — Simplify and Create Mental Model Diagram (~10 min)

Create a single mental model diagram that ties everything together. This should be the "aha moment" visual.

**Mental Model Diagram (Mermaid):**
- Show the 4 layers: Your Code -> SDK Entry Point -> Query Engine -> Transport -> CLI
- Show hooks as side-channels at the Query Engine layer
- Show MCP tools as extensions at the Query Engine layer
- Use color coding or labels for "you write this" vs "SDK handles this" vs "CLI handles this"

**Simplification pass:**
- Re-read the entire document
- Remove any jargon that wasn't defined
- Replace passive voice with active voice
- Ensure every paragraph answers "so what?" — why does the reader care?
- Add a "What to do next" section pointing to the cheatsheet for hands-on code

**Verify:** Diagram renders in Mermaid. Document flows logically from analogies -> gaps -> resolutions -> mental model. Total word count is ~2000 (+/- 200).

## 6. Edge Cases & Error Handling

| Case | Trigger | Expected | Recovery |
|------|---------|----------|----------|
| Content too technical | Used jargon without definition, or explained implementation details | Target reader (2nd-year CS student test) would not understand | Apply "explain like I'm teaching a junior dev" filter; replace jargon with plain English |
| Content too long | Exceeded 2000 words | Violates format constraint | Cut: remove redundant explanations, merge similar paragraphs, move details to footnotes |
| Analogy is misleading | Analogy maps incorrectly to actual behavior (e.g., "fire-and-forget" implies no response) | Reader builds wrong mental model | Validate each analogy against actual code behavior; add "where the analogy breaks" callout |
| Source code changed since architecture analysis | SDK updated between task d0g and task 1ig | Gap resolutions may be inaccurate | Always verify gap resolutions against current source code, not just architecture doc |
| Reader doesn't know async | Content assumes async knowledge | Reader is lost from the start | Add explicit prerequisite statement at the top: "Prerequisite: Python async/await (asyncio basics)" |

## 7. Acceptance Criteria

- **Happy 1:** Given the Feynman learning content is written, When a Python async developer who has never used claude-agent-sdk reads it, Then they can explain what query(), ClaudeSDKClient, hooks, and MCP tools do without looking at documentation, and can write a working script using query()
- **Happy 2:** Given the content, Then it contains 4 clear analogies for core concepts (query, client, hooks, MCP), each with a "maps to in code" callout, and includes a mental model diagram that renders in Mermaid
- **Negative:** Given a reader who does not know Python async/await, When they start reading, Then the prerequisite section at the top explicitly states "Prerequisite: Python async/await" so they know to learn that first

## 8. Technical Notes

- Feynman method: (1) Choose concept, (2) Explain simply, (3) Identify gaps, (4) Simplify further. This task follows all 4 steps.
- Word count target: ~2000 words. Check with `wc -w`. Acceptable range: 1800-2200.
- Target audience precisely: "Python developer who can write `async def main(): await asyncio.gather(...)` but has never heard of claude-agent-sdk"
- Mental model diagram: use Mermaid `graph TD` (top-down) for the layered architecture view
- Analogies should be culturally neutral (avoid sports metaphors, country-specific references)
- The "2nd-year CS student test": after writing each section, ask "would a 2nd-year CS student who knows Python understand this?" If no, simplify.
- File location: `self-explores/learnings/` directory (may need to be created)

## 9. Risks

- **Risk:** Analogies oversimplify to the point of being wrong, causing readers to build incorrect mental models. **Mitigation:** Each analogy includes a "where the analogy breaks" note. Cross-validate each analogy against the source code, not just the architecture doc.
- **Risk:** The Feynman approach requires genuine understanding; if the writer doesn't fully grasp the SDK, the gaps will be superficial. **Mitigation:** Step 3 requires reading actual source code for each gap, not just architecture summaries. If a gap can't be resolved from the code, it's explicitly noted.
- **Risk:** 2000 words may not be enough to cover 4 concepts with analogies + gaps + resolutions + diagram. **Mitigation:** Focus on the mental model, not exhaustive detail. Each concept gets ~400 words (analogy + gap + resolution), leaving ~400 words for intro, diagram, and conclusion.
- **Risk:** The mental model diagram may oversimplify the layered architecture. **Mitigation:** The diagram is a complement to the text, not a replacement. Label it "simplified mental model" and link to the detailed sequence diagrams from task 554.

## Worklog

### [11:15] Bat dau + Hoan thanh
**Ket qua:**
- Viet Feynman learning content: 1880 tu (trong range 1800-2200)
- 4 Feynman steps day du:
  1. Simple explanations voi 5 analogies: query=Letter, Client=Phone, Transport=Pipe, Hooks=Guards, MCP=Toolbox
  2. Identified 4 gaps: control protocol, streaming model, hook pause mechanism, anyio
  3. Resolved gaps tu source code knowledge
  4. Mental model diagram (Mermaid graph TD) + simplification pass
- Code examples moi analogy (3-10 dong)
- "What to do next" section link den cheatsheet + workshop video

**Files tao:**
- self-explores/learnings/2026-03-21-claude-agent-sdk-feynman.md (1880 words)
