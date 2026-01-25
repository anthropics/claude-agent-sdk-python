"""Claude Code CLI runner for PR reviews."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of a Claude Code review run."""

    success: bool
    stdout: str
    stderr: str
    return_code: int


def build_review_prompt(
    reviewer_prompt: str,
    pr_title: str,
    pr_body: str,
    pr_number: int,
    base_ref: str,
    head_ref: str,
    changed_files: list[str],
) -> str:
    """Build the review prompt for Claude."""
    files_list = "\n".join(f"- {f}" for f in changed_files)

    return f"""You are reviewing Pull Request #{pr_number}: {pr_title}

Branch: {head_ref} → {base_ref}

PR Description:
{pr_body or "(No description provided)"}

Changed Files:
{files_list}

---

{reviewer_prompt}

---

INSTRUCTIONS:
1. Read the changed files to understand what was modified
2. Explore related files if needed (imports, tests, configs)
3. Post inline comments on specific lines using create_inline_comment tool
4. When done, submit your review using submit_review tool with one of:
   - APPROVE: Code looks good
   - REQUEST_CHANGES: Issues that must be fixed
   - COMMENT: Suggestions but no blocking issues

IMPORTANT: You MUST call submit_review at the end to complete the review.
"""


def build_mcp_config(
    github_token: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
) -> dict[str, Any]:
    """Build MCP configuration for the review."""
    # Get the path to the MCP server module
    mcp_server_path = Path(__file__).parent.parent / "mcp" / "github_review.py"

    return {
        "mcpServers": {
            "github_review": {
                "command": "python",
                "args": [str(mcp_server_path)],
                "env": {
                    "GITHUB_TOKEN": github_token,
                    "GITHUB_OWNER": owner,
                    "GITHUB_REPO": repo,
                    "PR_NUMBER": str(pr_number),
                    "HEAD_SHA": head_sha,
                },
            }
        }
    }


async def run_claude_review(
    repo_path: Path,
    prompt: str,
    mcp_config: dict[str, Any],
    anthropic_api_key: str,
) -> ReviewResult:
    """Run Claude Code CLI to perform the review."""
    # Write MCP config to a temporary file in the repo
    claude_dir = repo_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    mcp_config_path = claude_dir / "mcp-review.json"
    mcp_config_path.write_text(json.dumps(mcp_config, indent=2))

    # Write prompt to a file
    prompt_path = claude_dir / "review-prompt.md"
    prompt_path.write_text(prompt)

    # Build Claude CLI command
    cmd = [
        "claude",
        "--print",  # Print output instead of interactive mode
        "--dangerously-skip-permissions",  # Skip permission prompts
        "--mcp-config",
        str(mcp_config_path),
        "--allowedTools",
        "Read,Glob,Grep,mcp__github_review__create_inline_comment,mcp__github_review__submit_review",
        "-p",
        prompt,
    ]

    logger.info(f"Running Claude Code CLI in {repo_path}")
    logger.debug(f"Command: {' '.join(cmd)}")

    # Set up environment
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = anthropic_api_key

    # Run Claude CLI
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=repo_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    stdout_str = stdout.decode() if stdout else ""
    stderr_str = stderr.decode() if stderr else ""

    if process.returncode == 0:
        logger.info("Claude Code review completed successfully")
    else:
        logger.error(f"Claude Code review failed with code {process.returncode}")
        logger.error(f"stderr: {stderr_str}")

    return ReviewResult(
        success=process.returncode == 0,
        stdout=stdout_str,
        stderr=stderr_str,
        return_code=process.returncode or 0,
    )
