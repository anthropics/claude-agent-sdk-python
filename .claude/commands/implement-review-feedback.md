---
description: Autonomously implement all PR review feedback
allowed-tools: Read, Edit, Write, Bash(git:*), Bash(python:*), Bash(gh:*), Grep, Glob, TodoWrite
argument-hint: <PR number or review text>
---

## Context

- Current git status: !`git status`
- Current branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -5`

## Your Task

Autonomously implement ALL feedback from the PR review: $ARGUMENTS

You will work systematically through all review items, fix any test failures, and create a commit when done.

---

## Process

### 1. Parse Input and Fetch Review Data

**Smart Detection**:
- If `$ARGUMENTS` is a number (e.g., "328") → PR number, fetch with `gh pr view $ARGUMENTS --json reviews,author`
- If `$ARGUMENTS` contains github.com URL → extract PR number and fetch
- Otherwise → treat as direct review feedback text

**Extract from GitHub**:
```bash
gh pr view <number> --json reviews,author --repo anthropics/claude-agent-sdk-python
```

Parse the JSON to extract:
- All review comments
- File-specific feedback
- General PR-level comments
- Reviewer name/email for commit co-author

### 2. Categorize Feedback Items

Parse all feedback and categorize into:
- **Must Fix**: Critical issues, bugs, security problems
- **Should Fix**: Important improvements, performance issues, test coverage gaps
- **Nice to Have**: Style improvements, documentation enhancements, minor suggestions

Create a TodoWrite list with ALL items (don't skip any).

### 3. Autonomous Implementation Loop

For each feedback item (in priority order):

**a) State what you're addressing**
```
Implementing: [X/N] Optimize _schema_uses_refs() performance
```

**b) Locate relevant code**
- Use Grep to find affected files
- Read the files to understand context

**c) Make the changes**
- Use Edit or Write to implement the fix
- Follow existing code patterns and style
- Maintain type safety and test coverage

**d) Validate changes**
- Run quick validation: `python -m ruff check src/ tests/`
- If errors, fix them immediately
- Mark todo item as completed

**e) Handle test failures**
- If tests fail at any point, analyze the error
- Fix the failure automatically (don't stop)
- Continue to next item

### 4. Full Validation Suite

After implementing ALL items, run complete test suite:

```bash
# Format and lint
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/

# Type checking
python -m mypy src/

# Run tests
python -m pytest tests/
```

If any failures:
- Analyze and fix automatically
- Re-run until all pass
- Report what was fixed

### 5. Auto-Commit

Create a commit with descriptive message:

**Format**:
```
fix: address PR review feedback (#<PR-number>)

Implemented:
- Item 1 description (1-line summary)
- Item 2 description
- Item 3 description

<If reviewer info available>
Co-authored-by: Reviewer Name <email>
```

**Commands**:
```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: address PR review feedback (#328)

Implemented:
- Optimized _schema_uses_refs() with recursive traversal
- Added TODO comment to convert_output_format()
- Added "not functional yet" warning to README
- Improved error messages with examples
- Added integration test for query() with output_format

Co-authored-by: Reviewer Name <email@example.com>
EOF
)"
```

---

## Important Rules

### Do:
- ✅ Work through ALL items systematically (don't skip)
- ✅ Use TodoWrite to track progress
- ✅ Fix test failures automatically as you encounter them
- ✅ Maintain existing code style and patterns
- ✅ Run full test suite before committing
- ✅ Create commit with descriptive message
- ✅ Be thorough and complete

### Don't:
- ❌ Skip items because they seem hard
- ❌ Stop when tests fail (fix them)
- ❌ Ask for user confirmation (work autonomously)
- ❌ Make breaking changes without careful consideration
- ❌ Forget to update tests when changing code
- ❌ Leave TODOs without implementing them (unless explicitly a "TODO comment" task)

---

## Example Execution Flow

```
🔍 Parsing input: "328"
   → Detected PR number, fetching from GitHub...

📋 Fetched review from PR #328 (6 items)
   Must Fix (2):
   - Optimize _schema_uses_refs() performance
   - Add TODO to convert_output_format()

   Should Fix (3):
   - Add "not functional yet" warning to README
   - Improve error messages with examples
   - Add integration test

   Nice to Have (1):
   - Add error handling example

✅ Created todo list with 6 items

🔧 [1/6] Implementing: Optimize _schema_uses_refs() performance
   → Found in src/claude_agent_sdk/_internal/schema_utils.py:92
   → Changed from JSON serialization to recursive traversal
   → Tests passing ✓

🔧 [2/6] Implementing: Add TODO to convert_output_format()
   → Found in src/claude_agent_sdk/_internal/schema_utils.py:106
   → Added TODO comment explaining future integration
   → Tests passing ✓

🔧 [3/6] Implementing: Add "not functional yet" warning to README
   → Updated README.md structured outputs section
   → Tests passing ✓

⚠️  [4/6] Implementing: Improve error messages
   → Modified schema_utils.py error messages
   → Tests failed: test_schema_utils.py::test_invalid_type
   → Fixed: Updated test assertion to match new message
   → Tests passing ✓

🔧 [5/6] Implementing: Add integration test
   → Created test_query_with_output_format in test_client.py
   → Tests passing ✓

🔧 [6/6] Implementing: Add error handling example
   → Updated examples/structured_outputs.py
   → Tests passing ✓

✅ All items implemented (6/6)

🧪 Running full test suite...
   ✓ ruff check: 0 issues
   ✓ ruff format: 0 changes
   ✓ mypy: Success, 0 errors
   ✓ pytest: 151 passed, 2 skipped

📝 Creating commit...
   ✓ Committed: fix: address PR review feedback (#328)

✨ Done! All review feedback has been implemented and committed.
```

---

## Notes for This Project

**Test Commands**:
- Lint: `python -m ruff check src/ tests/ --fix`
- Format: `python -m ruff format src/ tests/`
- Type check: `python -m mypy src/`
- Tests: `python -m pytest tests/`

**File Structure**:
- Source: `src/claude_agent_sdk/`
- Tests: `tests/`
- Examples: `examples/`
- Docs: `README.md`

**Common Patterns**:
- Use Edit tool for existing files
- Use Write tool only for new files
- Read files before editing
- Deep copy for preventing mutations
- Type hints required (mypy strict mode)
