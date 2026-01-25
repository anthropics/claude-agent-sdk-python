"""Claude Code SDK runner for PR reviews."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    McpStdioServerConfig,
    ResultMessage,
    TextBlock,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of a Claude Code review run."""

    success: bool
    stdout: str
    stderr: str
    return_code: int


def build_review_prompt(
    reviewer_name: str,
    reviewer_prompt: str,
    pr_title: str,
    pr_body: str,
    pr_number: int,
    base_ref: str,
    head_ref: str,
    changed_files: list[str],
    reviewer_persona: str = "",
) -> str:
    """Build the review prompt for Claude."""
    files_list = "\n".join(f"- {f}" for f in changed_files)
    # Use persona (human-like name) if provided, otherwise fall back to reviewer_name
    display = reviewer_persona or reviewer_name

    return f"""You are **{display}**, a code reviewer for Pull Request #{pr_number}: {pr_title}

Branch: {head_ref} → {base_ref}

PR Description:
{pr_body or "(No description provided)"}

Changed Files:
{files_list}

---

YOUR REVIEW FOCUS:
{reviewer_prompt}

---

INSTRUCTIONS:
1. First, call get_existing_reviews to see what other reviewers have already commented
   - Avoid duplicating issues already raised by other reviewers
2. Read the changed files to understand what was modified
3. Explore related files if needed (imports, tests, configs)
4. Post inline comments on specific lines using create_inline_comment tool
   - Sign each comment as **{display}** to identify yourself
   - Only comment on issues not already mentioned by other reviewers
5. When done, submit your review using submit_review tool with one of:
   - APPROVE: Code looks good
   - REQUEST_CHANGES: Issues that must be fixed
   - COMMENT: Suggestions but no blocking issues
   - Sign your review summary as **{display}**

IMPORTANT: You MUST call submit_review at the end to complete the review.
"""


def build_mcp_servers(
    github_token: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
) -> dict[str, McpStdioServerConfig]:
    """Build MCP servers configuration for the review."""
    # Get the path to the MCP server module
    mcp_server_path = Path(__file__).parent.parent / "mcp" / "github_review.py"

    return {
        "github_review": McpStdioServerConfig(
            type="stdio",
            command="python3",
            args=[str(mcp_server_path)],
            env={
                "GITHUB_TOKEN": github_token,
                "GITHUB_OWNER": owner,
                "GITHUB_REPO": repo,
                "PR_NUMBER": str(pr_number),
                "HEAD_SHA": head_sha,
            },
        )
    }


async def run_claude_review(
    repo_path: Path,
    prompt: str,
    mcp_servers: dict[str, McpStdioServerConfig],
    anthropic_api_key: str,
) -> ReviewResult:
    """Run Claude Code SDK to perform the review."""
    logger.info(f"Running Claude Code SDK review in {repo_path}")

    # Stderr callback for debugging
    def log_stderr(line: str) -> None:
        if line.strip():
            logger.warning(f"Claude stderr: {line.rstrip()}")

    # Build environment with all Claude Code related vars from current process
    # This includes Vertex AI config, auth tokens, etc.
    claude_env = {
        k: v for k, v in os.environ.items()
        if k.startswith(("CLAUDE_", "ANTHROPIC_", "NODE_"))
    }
    logger.info(f"Passing {len(claude_env)} Claude-related env vars to SDK")

    # Configure SDK options with MCP for GitHub review
    options = ClaudeAgentOptions(
        cwd=str(repo_path),
        mcp_servers=mcp_servers,  # Enable MCP for GitHub review tools
        allowed_tools=[
            "Read",
            "Glob",
            "Grep",
            "mcp__github_review__get_existing_reviews",
            "mcp__github_review__create_inline_comment",
            "mcp__github_review__submit_review",
        ],
        permission_mode="bypassPermissions",  # Skip permission prompts
        stderr=log_stderr,  # Capture stderr for debugging
        env=claude_env,  # Pass all Claude/Anthropic env vars
        setting_sources=["user"],  # Load user settings for auth
    )

    output_text = ""
    is_error = False
    error_message = ""

    try:
        msg_count = 0
        async for message in query(prompt=prompt, options=options):
            msg_count += 1
            msg_type = type(message).__name__

            # Log message content preview (max 200 chars)
            content_preview = ""
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        content_preview = block.text[:200].replace("\n", " ")
                        output_text += block.text + "\n"
                        break
            elif hasattr(message, "content") and isinstance(message.content, str):
                content_preview = message.content[:200].replace("\n", " ")

            if isinstance(message, ResultMessage):
                logger.info(
                    f"#{msg_count} ResultMessage: turns={message.num_turns}, "
                    f"cost=${message.total_cost_usd or 0:.4f}, "
                    f"duration={message.duration_ms}ms, "
                    f"is_error={message.is_error}"
                )
                is_error = message.is_error
                if is_error and message.result:
                    error_message = message.result
                    logger.error(f"Error result: {message.result}")
            else:
                logger.info(f"#{msg_count} {msg_type}: {content_preview[:200] if content_preview else '(no text)'}")

        logger.info(f"Query iteration complete. Total messages: {msg_count}")
        return ReviewResult(
            success=not is_error,
            stdout=output_text,
            stderr=error_message,
            return_code=1 if is_error else 0,
        )

    except Exception as e:
        logger.error(f"Claude Code review failed: {e}")
        return ReviewResult(
            success=False,
            stdout=output_text,
            stderr=str(e),
            return_code=1,
        )
