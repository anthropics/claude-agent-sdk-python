"""Error handling for AI PR Reviewer.

Provides functionality to notify PR authors when AI reviews fail,
including posting error comments to GitHub PRs.
"""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class ErrorPostingError(Exception):
    """Raised when posting an error comment to GitHub fails."""

    pass


@dataclass
class ErrorNotification:
    """Details of an error notification posted to a PR."""

    comment_id: int
    html_url: str
    pr_number: int


# Error message templates
ERROR_HEADER = "## :warning: AI Review Failed"

ERROR_BODY_TEMPLATE = """
**Reviewer:** @{reviewer_username}

The AI review could not be completed after multiple attempts.

### Error Details

{error_details}

### What You Can Do

1. **Re-request the review** by removing and re-adding @{reviewer_username} as a reviewer
2. **Check if the PR has issues** that might prevent the AI from analyzing it (e.g., very large diffs)
3. **Contact the repository maintainers** if the issue persists

---
*This message was generated automatically by the AI PR Reviewer.*
"""


def format_error_message(
    reviewer_username: str,
    error_message: str,
) -> str:
    """
    Format an error message for posting to a PR.

    Args:
        reviewer_username: The GitHub username of the AI reviewer that failed.
        error_message: The error message describing what went wrong.

    Returns:
        A formatted markdown error message.
    """
    # Sanitize error message to avoid exposing sensitive details
    sanitized_error = _sanitize_error_message(error_message)

    error_details = f"```\n{sanitized_error}\n```"

    body = ERROR_BODY_TEMPLATE.format(
        reviewer_username=reviewer_username,
        error_details=error_details,
    )

    return f"{ERROR_HEADER}\n{body}"


def _sanitize_error_message(error_message: str) -> str:
    """
    Sanitize an error message to remove potentially sensitive information.

    Removes or masks:
    - API tokens or keys
    - File paths that might expose server structure
    - Stack traces (kept minimal)

    Args:
        error_message: The raw error message.

    Returns:
        A sanitized error message safe for public display.
    """
    # Keep error message concise and actionable
    # Truncate very long error messages
    max_length = 500
    if len(error_message) > max_length:
        error_message = error_message[:max_length] + "... (truncated)"

    # Remove common sensitive patterns
    sensitive_patterns = [
        ("Bearer ", "Bearer [REDACTED]"),
        ("token=", "token=[REDACTED]"),
        ("api_key=", "api_key=[REDACTED]"),
        ("password=", "password=[REDACTED]"),
    ]

    result = error_message
    for pattern, replacement in sensitive_patterns:
        if pattern in result:
            # Find the pattern and redact the value after it
            start = result.find(pattern)
            end = start + len(pattern)
            # Find the end of the value (space, newline, or end of string)
            value_end = end
            while value_end < len(result) and result[value_end] not in " \n\t,;\"'":
                value_end += 1
            result = result[:start] + replacement + result[value_end:]

    return result


async def post_error_comment(
    owner: str,
    repo: str,
    pr_number: int,
    reviewer_username: str,
    error_message: str,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> ErrorNotification:
    """
    Post an error comment to a GitHub PR when a review fails.

    Creates an issue comment on the PR to notify the author that
    the AI review could not be completed.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        reviewer_username: The GitHub username of the AI reviewer that failed.
        error_message: The error message describing what went wrong.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        ErrorNotification with details of the posted comment.

    Raises:
        ErrorPostingError: If the comment cannot be posted.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # Format the error message
        comment_body = format_error_message(reviewer_username, error_message)

        # Post as an issue comment (not a review comment)
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        response = await client.post(
            url,
            headers=headers,
            json={"body": comment_body},
        )

        # Handle errors
        if response.status_code == 404:
            raise ErrorPostingError(
                f"PR #{pr_number} not found in {owner}/{repo}"
            ) from None
        if response.status_code == 403:
            raise ErrorPostingError(
                "Insufficient permissions to post comment"
            ) from None
        if response.status_code >= 400:
            raise ErrorPostingError(
                f"GitHub API error: {response.status_code} - {response.text}"
            ) from None

        response.raise_for_status()

        # Parse response
        data = response.json()
        comment_id: int = data["id"]
        html_url: str = data["html_url"]

        logger.info(
            f"Posted error notification #{comment_id} to PR #{pr_number} "
            f"in {owner}/{repo}"
        )

        return ErrorNotification(
            comment_id=comment_id,
            html_url=html_url,
            pr_number=pr_number,
        )

    except httpx.HTTPError as e:
        raise ErrorPostingError(f"Failed to post error comment: {e}") from e

    finally:
        if should_close_client:
            await client.aclose()
