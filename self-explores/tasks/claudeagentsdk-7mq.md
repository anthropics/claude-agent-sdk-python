---
date: 2026-03-21
type: task-worklog
task: claudeagentsdk-7mq
title: "Ve Use Case Diagram"
status: open
detailed_at: 2026-03-21
detail_score: ready-for-dev
tags: [diagram, use-case, uml, draw-io, mermaid, phase-2]
---

# Ve Use Case Diagram — Detailed Design

## 1. Objective

Create a UML Use Case diagram with correctly classified actors (primary, system, external) showing all major SDK capabilities, using draw.io MCP as primary tool with Mermaid flowchart as fallback.

## 2. Scope

**In-scope:**
- 3 actors with correct UML classification (Developer = primary, Claude Code CLI = system, External MCP Server = external)
- 8+ use cases covering the full SDK capability surface
- Actor-to-use-case relationships (association, include, extend)
- Grouping use cases by subsystem boundary
- Both draw.io and Mermaid fallback approaches

**Out-of-scope:**
- Activity diagrams or state diagrams (different task)
- Internal implementation details within use cases
- Non-SDK use cases (e.g., direct CLI usage without the SDK)
- Hook System as an actor (it is a subsystem/mechanism, not an actor)
- Detailed use case specifications/narratives (covered in task qw0)

## 3. Input / Output

**Input:**
- `self-explores/context/use-case-guide.md` (from task qw0) — analyzed use cases with actor mapping
- SDK public API surface from `src/claude_agent_sdk/__init__.py` and `src/claude_agent_sdk/client.py`

**Output:**
- Primary: draw.io diagram file (if MCP available) at `self-explores/tasks/claudeagentsdk-7mq-usecase.drawio` or equivalent
- Fallback: `self-explores/tasks/claudeagentsdk-7mq-usecase.md` containing Mermaid flowchart styled as use case diagram
- Both: descriptive text explaining actor classification rationale

## 4. Dependencies

- `claudeagentsdk-qw0` (use case guide) — MUST be completed first; provides the analyzed use cases and actor identification
- Tool dependencies: `mcp__drawio__open_drawio_mermaid` (primary), Mermaid syntax (fallback)

## 5. Flow

### Step 1: Define Actors with UML Classification (~5 min)

Establish the 3 actors with correct UML stereotypes:

1. **Developer** (Primary Actor, stick figure on LEFT)
   - The human user who writes Python code using the SDK
   - Initiates all use cases
   - Stereotype: `<<primary>>`

2. **Claude Code CLI** (System Actor, stick figure on RIGHT or box)
   - The subprocess that the SDK wraps
   - Participates in all use cases as the execution backend
   - Stereotype: `<<system>>`

3. **External MCP Server** (External Actor, stick figure on RIGHT)
   - Third-party MCP servers connected via subprocess
   - Only participates in external MCP use cases
   - Stereotype: `<<external>>`

**Why NOT Hook System as actor:** In UML, an actor is an entity outside the system boundary that interacts with it. The Hook System is internal to the SDK — it is a mechanism/subsystem, not an actor. Hooks are triggered by the Developer (who defines them) and the CLI (which sends hook callbacks).

**Verify:** Actor list matches UML conventions. No internal subsystems misclassified as actors.

### Step 2: List and Categorize Use Cases (~10 min)

Extract use cases from qw0 output and organize by subsystem:

**Core Query Subsystem:**
- UC1: Execute Simple Query (Developer -> CLI)
- UC2: Run Interactive Conversation (Developer -> CLI)
- UC3: Stream Response Messages (Developer -> CLI)

**Tool & Extension Subsystem:**
- UC4: Define Custom MCP Tools (Developer)
- UC5: Connect External MCP Servers (Developer -> External MCP Server)
- UC6: Orchestrate Agent with Tools (Developer -> CLI -> External MCP Server)

**Control & Safety Subsystem:**
- UC7: Control Tool Permissions via Hooks (Developer -> CLI)
- UC8: Manage Permission Mode (Developer -> CLI)
- UC9: Set Budget/Token Limits (Developer -> CLI)

**Session & Configuration Subsystem:**
- UC10: Customize System Prompt (Developer -> CLI)
- UC11: Manage Session State (Developer -> CLI)
- UC12: Configure Model Parameters (Developer -> CLI)

Identify `<<include>>` and `<<extend>>` relationships:
- UC2 `<<include>>` UC3 (interactive always streams)
- UC6 `<<include>>` UC5 (agent orchestration requires MCP)
- UC7 `<<extend>>` UC1 (hooks optionally extend queries)
- UC9 `<<extend>>` UC1 (budget optionally extends queries)

**Verify:** At least 8 unique use cases identified. Each use case has at least one actor association. Include/extend relationships are semantically correct.

### Step 3: Draw Diagram with draw.io MCP (~10 min)

**Primary approach — draw.io MCP:**

Use `mcp__drawio__open_drawio_mermaid` to create the diagram. Since draw.io MCP accepts Mermaid syntax, prepare a Mermaid graph that represents the use case diagram:

```
graph LR
    subgraph "claude-agent-sdk System Boundary"
        UC1[Execute Simple Query]
        UC2[Run Interactive Conversation]
        ...
    end
    Developer((Developer)) --> UC1
    CLI((Claude Code CLI)) --> UC1
    ...
```

Style actors as circles `(( ))`, use cases as rounded rectangles `[ ]`, system boundary as `subgraph`.

**Fallback approach — Mermaid in Markdown:**

If `mcp__drawio__open_drawio_mermaid` fails or is unavailable, create the same diagram as a Mermaid flowchart in a Markdown file. Use:
- `graph LR` (left-to-right layout)
- `subgraph` for system boundary
- `(( ))` for actors (circle shape)
- `([  ])` for use cases (stadium shape, closest to UML oval)
- `-.->` for `<<extend>>`, `-->` for association, `==>` for `<<include>>`

**Verify:** Diagram renders without errors. All 3 actors visible. All use cases inside system boundary. Relationships have correct arrow types.

### Step 4: Review and Add Descriptions (~5 min)

- Add a legend explaining actor stereotypes and relationship types
- Add a brief description (1 sentence) for each use case below the diagram
- Verify completeness: every use case from Step 2 appears in the diagram
- Verify correctness: no use case has an impossible actor association
- Add title and date to the output file

**Verify:** Legend present. All use cases have descriptions. Actor associations are plausible.

## 6. Edge Cases & Error Handling

| Case | Trigger | Expected | Recovery |
|------|---------|----------|----------|
| draw.io MCP not available | MCP server not configured or connection fails | Cannot create draw.io diagram | Fall back to Mermaid flowchart in Markdown file immediately |
| Too many use cases | >15 use cases identified | Diagram becomes cluttered and unreadable | Group by subsystem using nested `subgraph` blocks; limit to 12 most important |
| Mermaid has no native UC diagram | Mermaid syntax lacks `usecase` keyword | Cannot create true UML use case diagram | Use `graph LR` with styled nodes: `(( ))` for actors, `([  ])` for use cases |
| Use case guide (qw0) incomplete | Task qw0 not fully finished or output missing | Missing use cases or actor info | Extract use cases directly from SDK public API surface as backup |
| draw.io MCP produces unreadable layout | Auto-layout places elements poorly | Diagram hard to understand | Add explicit positioning hints or switch to Mermaid where layout is more predictable |

## 7. Acceptance Criteria

- **Happy 1:** Given use cases analyzed in task qw0, When diagram is created, Then it contains 3+ correctly classified actors (primary/system/external), 8+ use cases, and correct UML relationships (association, include, extend)
- **Happy 2:** Given the completed diagram, When a developer or PM views it, Then they can identify all major SDK capabilities and which actors are involved without reading documentation
- **Negative:** Given draw.io MCP is unavailable, When fallback to Mermaid flowchart is used, Then the Mermaid diagram still represents all 3 actors, 8+ use cases, and renders correctly on GitHub

## 8. Technical Notes

- draw.io MCP tool: `mcp__drawio__open_drawio_mermaid` — accepts Mermaid syntax and opens in draw.io for visual editing
- Mermaid does NOT have a native `usecaseDiagram` type (unlike PlantUML); must simulate with `graph` or `flowchart`
- PlantUML has native `@startuml` use case syntax but is not natively rendered on GitHub — Mermaid is preferred for GitHub compatibility
- For the Mermaid fallback, use this node styling convention:
  - Actors: `ActorName((Actor Name))` (double parentheses = circle)
  - Use cases: `UC1([Use Case Name])` (parentheses + brackets = stadium/oval shape)
  - System boundary: `subgraph "System Name"` ... `end`
- Arrow conventions: `-->` solid = association, `-.->|<<extend>>|` dashed = extend, `-->|<<include>>|` labeled solid = include

## 9. Risks

- **Risk:** draw.io MCP may not be configured in the current environment, wasting time on the primary approach. **Mitigation:** Test MCP availability with a simple call before investing time in draw.io-specific formatting. Budget only 2 minutes for the test.
- **Risk:** Mermaid flowchart looks too different from standard UML use case diagram, confusing readers familiar with UML. **Mitigation:** Add a clear legend and note stating "Rendered as flowchart due to Mermaid limitations; follows UML use case semantics."
- **Risk:** Use cases from task qw0 may not cover all SDK capabilities if qw0 analysis was incomplete. **Mitigation:** Cross-reference use cases against the SDK's public API (`__init__.py` exports) to catch gaps.

## Worklog

### [10:40] Bat dau
- Doc use-case-guide.md + task worklog chi tiet

### [10:50] Hoan thanh — Use Case Diagram
**Ket qua:**
- Tao `claudeagentsdk-7mq-usecase.md` voi Mermaid use case diagram
- Mo draw.io MCP thanh cong — diagram render trong browser

**Diagram:**
- 3 actors dung UML: Developer (primary), Claude Code CLI (system), External MCP Server (external)
- 12 use cases chia 4 subsystems:
  - Core Query (3): Simple Query, Interactive Conversation, Stream Messages
  - Tool & Extension (3): Custom MCP Tools, External MCP, Agent Orchestration
  - Control & Safety (4): Hooks, Permission Mode, Budget, Tool Permissions
  - Session & Config (2): System Prompt/Model, Session State
- 2 include relationships: UC2→UC3, UC6→UC5
- 3 extend relationships: UC7→UC1, UC9→UC1, UC10→UC2
- Color-coded: green (core), blue (tools), red (control), purple (config)
- Legend + descriptions table cho moi use case

**Files tao:**
- self-explores/tasks/claudeagentsdk-7mq-usecase.md (Mermaid + descriptions)
- draw.io diagram opened via MCP
