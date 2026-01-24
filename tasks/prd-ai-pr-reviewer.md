# PRD: AI PR Reviewer GitHub App (MVP)

## Introduction

A GitHub App that performs AI-powered PR reviews when specific users are requested as reviewers. The app acts "on behalf of" configured users, using Claude Code Python SDK to generate reviews with summaries and inline comments. Each configured user has their own custom review prompt, and reviews are processed in a queue to prevent duplicates.

## Goals

- Provide automated AI PR reviews triggered by reviewer assignment
- Support per-user custom prompts for personalized review styles
- Generate reviews with both summary comments and inline code suggestions
- Ensure ordered, non-duplicate reviews via queue-based processing
- Enable org-wide installation with simple per-repo configuration

## User Stories

### US-001: GitHub App webhook server setup
**Description:** As a developer, I need a webhook server that receives GitHub events so that the app can respond to PR reviewer assignments.

**Acceptance Criteria:**
- [ ] FastAPI server with `/webhook` endpoint
- [ ] HMAC-SHA256 signature verification for incoming webhooks
- [ ] Support for `pull_request` events (specifically `review_requested` action)
- [ ] Health check endpoint at `/health`
- [ ] Typecheck passes

### US-002: GitHub App authentication
**Description:** As a developer, I need the app to authenticate with GitHub API so that it can read PRs and post reviews.

**Acceptance Criteria:**
- [ ] GitHub App JWT generation from private key
- [ ] Installation access token retrieval per repository
- [ ] Token caching with expiration handling
- [ ] Typecheck passes

### US-003: Reviewer configuration via repo config file
**Description:** As a repo maintainer, I want to configure AI reviewers via a YAML file so that I can map GitHub usernames to AI prompts.

**Acceptance Criteria:**
- [ ] Parse `.ai-reviewer.yml` from repo root
- [ ] Support reviewer mapping: GitHub username → custom prompt
- [ ] Support optional language setting per reviewer
- [ ] Typecheck passes

### US-004: Review trigger detection
**Description:** As a user, I want AI reviews triggered when I request specific users as reviewers.

**Acceptance Criteria:**
- [ ] Detect `review_requested` webhook action
- [ ] Check if requested reviewer is in configured AI reviewer list
- [ ] Support multiple AI reviewers requested simultaneously
- [ ] Ignore non-configured reviewers
- [ ] Typecheck passes

### US-005: Review queue implementation
**Description:** As a developer, I need a queue system for reviews so that they process in order without duplicates.

**Acceptance Criteria:**
- [ ] Queue data structure (in-memory with optional Redis)
- [ ] Unique job ID per (PR, reviewer) combination
- [ ] FIFO processing order
- [ ] Duplicate detection: skip if same (PR, reviewer) already queued or in-progress
- [ ] Job status tracking: queued, in_progress, completed, failed
- [ ] Typecheck passes

### US-006: Queue worker for review processing
**Description:** As a developer, I need a worker that processes queued reviews sequentially.

**Acceptance Criteria:**
- [ ] Background worker consuming from queue
- [ ] Lock mechanism to prevent concurrent reviews on same PR
- [ ] Basic retry logic for transient failures
- [ ] Typecheck passes

### US-007: PR context gathering
**Description:** As the AI reviewer, I need full PR context to provide informed reviews.

**Acceptance Criteria:**
- [ ] Fetch PR metadata (title, body, author, base/head branches)
- [ ] Fetch all changed files with diffs
- [ ] Fetch commit history for the PR
- [ ] Typecheck passes

### US-008: Claude Code Python SDK integration
**Description:** As a developer, I need to integrate with Claude Code Python SDK for review generation.

**Acceptance Criteria:**
- [ ] Initialize Claude Code SDK client
- [ ] Pass PR context and user-specific prompt to Claude
- [ ] Parse structured review output (summary + inline comments)
- [ ] Handle SDK errors gracefully
- [ ] Typecheck passes

### US-009: Review prompt construction
**Description:** As a developer, I need to construct review prompts with user customization and PR context.

**Acceptance Criteria:**
- [ ] Base review prompt template
- [ ] Inject user-specific custom instructions from config
- [ ] Include PR diff context in prompt
- [ ] Typecheck passes

### US-010: Review summary generation
**Description:** As a PR author, I want a summary comment on my PR with the AI's overall feedback.

**Acceptance Criteria:**
- [ ] Generate markdown summary from Claude response
- [ ] Include persona label header (e.g., "**Review on behalf of @alice-ai**")
- [ ] Include overall assessment (approve, request changes, comment)
- [ ] List key findings
- [ ] Typecheck passes

### US-011: Inline comment generation
**Description:** As a PR author, I want inline comments on specific code lines.

**Acceptance Criteria:**
- [ ] Parse Claude response for file-specific suggestions
- [ ] Map suggestions to correct file paths and line numbers
- [ ] Support code suggestion blocks (```suggestion format)
- [ ] Typecheck passes

### US-012: Post review to GitHub
**Description:** As a developer, I need to post the complete review to GitHub.

**Acceptance Criteria:**
- [ ] Create PR review with summary body
- [ ] Attach inline comments to the review
- [ ] Set review state (COMMENT, APPROVE, REQUEST_CHANGES)
- [ ] Handle GitHub API rate limits
- [ ] Typecheck passes

### US-013: Review deduplication
**Description:** As a PR author, I don't want duplicate reviews from the same AI persona.

**Acceptance Criteria:**
- [ ] Track completed reviews per (PR, reviewer, commit SHA)
- [ ] Skip review if already reviewed at current HEAD
- [ ] Typecheck passes

### US-014: Error handling
**Description:** As a PR author, I want to be notified if the AI review fails.

**Acceptance Criteria:**
- [ ] Post comment on PR if review fails after retries
- [ ] Include actionable error message
- [ ] Typecheck passes

## Functional Requirements

- FR-1: The app must authenticate as a GitHub App using JWT and installation tokens
- FR-2: The app must verify webhook signatures using HMAC-SHA256
- FR-3: The app must trigger reviews only when configured AI reviewer usernames are requested
- FR-4: The app must read reviewer configuration from `.ai-reviewer.yml` in repo root
- FR-5: The app must process reviews in FIFO queue order with deduplication
- FR-6: The app must use Claude Code Python SDK for generating review content
- FR-7: The app must post reviews with both summary and inline comments
- FR-8: The app must prevent duplicate reviews for the same PR + reviewer + commit

## Non-Goals (MVP)

- No multiple GitHub App identities (single bot posts all reviews with persona labels)
- No web UI for configuration (use YAML files only)
- No org-level default configuration
- No environment variable configuration
- No stale comment handling (resolve/delete old comments)
- No PR filtering (draft, WIP, file count, paths, authors)
- No severity filtering for comments
- No comment count limits
- No GitHub Enterprise support (github.com only)
- No automatic code fixes or commits

## Technical Considerations

- **Framework**: FastAPI for webhook server
- **Queue**: In-memory queue (Redis optional for production)
- **SDK**: Claude Code Python SDK (`claude-code-sdk-python`)
- **Deployment**: Docker container
- **Identity**: Single GitHub App - all reviews posted by `app-name[bot]`, with persona labels in comment content

## Configuration Format

`.ai-reviewer.yml`:
```yaml
reviewers:
  alice-ai:
    prompt: |
      你是一个专注于安全的审查员。
      重点关注 SQL 注入、XSS 等安全漏洞。
    language: zh-CN

  bob-ai:
    prompt: |
      Review for code quality and best practices.
      Focus on readability and maintainability.
    language: en

# 审查顺序（按此顺序执行）
review_order:
  - alice-ai
  - bob-ai
```

## Success Metrics

- Reviews posted within 2 minutes of reviewer assignment
- Zero duplicate reviews for same (PR, reviewer, commit)

## Open Questions

- Should the app remove itself from the reviewer list after posting review?
