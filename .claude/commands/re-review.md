---
description: Verify all PR review items were implemented correctly
allowed-tools: Read, Bash(git:*), Bash(python:*), Bash(gh:*), Grep, Glob
argument-hint: <PR number>
---

## Context

- Current branch: !`git branch --show-current`
- Git diff since main: !`git diff main...HEAD --stat`
- Recent commits: !`git log --oneline -10`

## Your Task

Verify that ALL items from the original PR review were correctly implemented: $ARGUMENTS

You will fetch the original review, verify each item through multiple methods, and post a detailed verification report as a PR comment.

---

## Process

### 1. Fetch Original Review

**Get PR review data**:
```bash
gh pr view $ARGUMENTS --json reviews,comments,number,title --repo anthropics/claude-agent-sdk-python
```

**Parse the JSON** to extract:
- All review comments (file-level and general)
- Review body text
- Reviewer information
- PR number and title

### 2. Parse All Feedback Items

Extract every actionable item from the review and categorize:
- **Must Fix**: Critical issues, bugs, security, test failures
- **Should Fix**: Performance, code quality, test coverage, documentation
- **Nice to Have**: Style, minor refactoring, optional enhancements

### 3. Verify Each Item (Multi-Method Verification)

For **each feedback item**:

#### A. Git Diff Analysis
```bash
git diff main...HEAD --name-only
git diff main...HEAD -- path/to/file.py
```

#### B. Code Inspection
- Read affected files
- Verify implementation approach
- Check code quality

#### C. Test Execution
```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest tests/ -v
```

#### D. Example Execution (if applicable)
```bash
python examples/structured_outputs.py
```

#### E. Assessment
- ✅ **Verified**: Correctly implemented
- ⚠️ **Partial**: Addressed with minor concerns
- ❌ **Not Implemented**: Not addressed

### 4. Generate Detailed Verification Report

Format:
```markdown
# 🔍 Re-Review Verification Report

**PR**: #<number>
**Verification Date**: <date>

## 📊 Summary
- ✅ Verified: X/N items
- ⚠️ Partial: Y/N items
- ❌ Not Implemented: Z/N items

## 📋 Item-by-Item Verification

### ✅ 1. <Item Title>

**Original Feedback**: > <quote>

**Verification**:
- **Git Diff**: <files changed>
- **Code Inspection**: <implementation details>
- **Tests**: <results>
- **Assessment**: <why ✅/⚠️/❌>

[Repeat for all items]

## 🧪 Test Results
<full test output>

## 💡 Recommendations
<suggestions>

## 📝 Conclusion
<summary and recommendation>
```

### 5. Post Report

```bash
gh pr review $ARGUMENTS --comment --body "<report>" --repo anthropics/claude-agent-sdk-python
```

---

## Important Rules

### Do:
- ✅ Verify EVERY item (don't skip)
- ✅ Use multiple verification methods
- ✅ Be thorough and specific
- ✅ Quote original feedback exactly
- ✅ Run full test suite
- ✅ Post complete report

### Don't:
- ❌ Skip items
- ❌ Make assumptions without checking
- ❌ Be vague
- ❌ Miss edge cases
