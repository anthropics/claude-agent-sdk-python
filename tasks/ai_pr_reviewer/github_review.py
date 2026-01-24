"""GitHub review posting for AI PR Reviewer.

Provides functionality to post complete PR reviews to GitHub,
including summary body, inline comments, and review state.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from .inline_comments import MappedInlineComment
from .review_summary import ReviewSummary

logger = logging.getLogger(__name__)


class GitHubReviewError(Exception):
    """Raised when posting a review to GitHub fails."""

    pass


class GitHubRateLimitError(GitHubReviewError):
    """Raised when GitHub API rate limit is exceeded."""

    def __init__(
        self,
        message: str,
        reset_at: float | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.retry_after = retry_after


# Mapping from internal assessment to GitHub review event
REVIEW_STATE_MAP: dict[str, str] = {
    "approve": "APPROVE",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}


@dataclass
class ReviewComment:
    """A comment to attach to a PR review.

    This matches GitHub's expected format for review comments.
    """

    path: str
    line: int
    body: str
    side: str = "RIGHT"
    start_line: int | None = None
    start_side: str | None = None


@dataclass
class PostedReview:
    """Result of posting a review to GitHub."""

    review_id: int
    html_url: str
    state: str
    submitted_at: str
    comments_count: int


@dataclass
class RateLimitInfo:
    """GitHub API rate limit information."""

    limit: int
    remaining: int
    reset: float  # Unix timestamp
    used: int

    @property
    def is_exceeded(self) -> bool:
        """Check if rate limit is exceeded."""
        return self.remaining == 0

    @property
    def reset_in_seconds(self) -> float:
        """Seconds until rate limit resets."""
        return max(0, self.reset - time.time())


def _parse_rate_limit_headers(headers: httpx.Headers) -> RateLimitInfo | None:
    """
    Parse rate limit information from GitHub API response headers.

    Args:
        headers: The response headers from a GitHub API call.

    Returns:
        RateLimitInfo if headers are present, None otherwise.
    """
    try:
        limit = int(headers.get("x-ratelimit-limit", "0"))
        remaining = int(headers.get("x-ratelimit-remaining", "0"))
        reset = float(headers.get("x-ratelimit-reset", "0"))
        used = int(headers.get("x-ratelimit-used", "0"))

        if limit == 0:
            return None

        return RateLimitInfo(
            limit=limit,
            remaining=remaining,
            reset=reset,
            used=used,
        )
    except (ValueError, TypeError):
        return None


def _build_review_comments(
    inline_comments: list[MappedInlineComment],
) -> list[dict[str, str | int | None]]:
    """
    Build the comments array for the GitHub review API.

    Args:
        inline_comments: List of mapped inline comments.

    Returns:
        List of comment dictionaries for the API payload.
    """
    comments: list[dict[str, str | int | None]] = []

    for comment in inline_comments:
        comment_dict: dict[str, str | int | None] = {
            "path": comment.path,
            "line": comment.line,
            "body": comment.body,
            "side": comment.side,
        }

        # Add multi-line comment fields if present
        if comment.start_line is not None:
            comment_dict["start_line"] = comment.start_line
            comment_dict["start_side"] = comment.side

        comments.append(comment_dict)

    return comments


async def _wait_for_rate_limit_reset(
    rate_limit: RateLimitInfo,
    max_wait_seconds: float = 60.0,
) -> bool:
    """
    Wait for rate limit to reset if within acceptable wait time.

    Args:
        rate_limit: Current rate limit information.
        max_wait_seconds: Maximum seconds to wait for reset.

    Returns:
        True if waited and can retry, False if wait would be too long.
    """
    wait_seconds = rate_limit.reset_in_seconds

    if wait_seconds > max_wait_seconds:
        return False

    logger.info(f"Rate limit exceeded, waiting {wait_seconds:.1f}s for reset")
    await asyncio.sleep(wait_seconds + 1)  # Add 1 second buffer
    return True


async def post_review(
    owner: str,
    repo: str,
    pr_number: int,
    commit_id: str,
    summary: ReviewSummary,
    inline_comments: list[MappedInlineComment],
    token: str,
    http_client: httpx.AsyncClient | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> PostedReview:
    """
    Post a complete PR review to GitHub.

    Creates a review with the summary as the body, inline comments
    attached to specific lines, and the appropriate review state.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        commit_id: The SHA of the commit to review (usually head SHA).
        summary: The review summary with markdown body and assessment.
        inline_comments: List of mapped inline comments to attach.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.
        max_retries: Maximum number of retries for transient failures.
        retry_delay: Base delay between retries in seconds.

    Returns:
        PostedReview with the created review details.

    Raises:
        GitHubReviewError: If the review cannot be posted.
        GitHubRateLimitError: If rate limit is exceeded and cannot wait.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # Build the review payload
        event = REVIEW_STATE_MAP.get(summary.overall_assessment, "COMMENT")
        comments = _build_review_comments(inline_comments)

        payload: dict[str, str | list[dict[str, str | int | None]]] = {
            "body": summary.markdown,
            "event": event,
            "commit_id": commit_id,
        }

        if comments:
            payload["comments"] = comments

        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                )

                # Check for rate limiting
                if response.status_code == 403:
                    rate_limit = _parse_rate_limit_headers(response.headers)
                    if rate_limit and rate_limit.is_exceeded:
                        if await _wait_for_rate_limit_reset(rate_limit):
                            continue  # Retry after waiting
                        raise GitHubRateLimitError(
                            "GitHub API rate limit exceeded",
                            reset_at=rate_limit.reset,
                            retry_after=int(rate_limit.reset_in_seconds),
                        )

                # Check for secondary rate limiting (abuse detection)
                if response.status_code == 403:
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        wait_seconds = int(retry_after)
                        if wait_seconds <= 60:
                            logger.info(
                                f"Secondary rate limit, waiting {wait_seconds}s"
                            )
                            await asyncio.sleep(wait_seconds)
                            continue
                        raise GitHubRateLimitError(
                            "GitHub secondary rate limit exceeded",
                            retry_after=wait_seconds,
                        )

                # Check for server errors (5xx) - retry these
                if response.status_code >= 500:
                    delay = retry_delay * (2**attempt)
                    logger.warning(
                        f"GitHub server error {response.status_code}, "
                        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Check for validation errors
                if response.status_code == 422:
                    error_data = response.json()
                    errors = error_data.get("errors", [])
                    message = error_data.get("message", "Validation failed")
                    error_details = "; ".join(str(e.get("message", e)) for e in errors)
                    raise GitHubReviewError(
                        f"GitHub validation error: {message}. {error_details}"
                    )

                response.raise_for_status()

                # Parse successful response
                data = response.json()
                review_id: int = data["id"]
                html_url: str = data["html_url"]
                state: str = data["state"]
                submitted_at: str = data.get("submitted_at", "")

                logger.info(
                    f"Posted review #{review_id} to PR #{pr_number} "
                    f"with {len(inline_comments)} inline comments"
                )

                return PostedReview(
                    review_id=review_id,
                    html_url=html_url,
                    state=state,
                    submitted_at=submitted_at,
                    comments_count=len(inline_comments),
                )

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code >= 500:
                    delay = retry_delay * (2**attempt)
                    logger.warning(
                        f"GitHub HTTP error {e.response.status_code}, "
                        f"retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                raise GitHubReviewError(
                    f"Failed to post review: {e.response.status_code} - "
                    f"{e.response.text}"
                ) from e

            except httpx.RequestError as e:
                last_error = e
                delay = retry_delay * (2**attempt)
                logger.warning(
                    f"Network error posting review: {e}, "
                    f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)
                continue

        # All retries exhausted
        raise GitHubReviewError(
            f"Failed to post review after {max_retries} attempts"
        ) from last_error

    finally:
        if should_close_client:
            await client.aclose()


async def get_rate_limit_status(
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> RateLimitInfo:
    """
    Get current GitHub API rate limit status.

    Args:
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        RateLimitInfo with current rate limit status.

    Raises:
        GitHubReviewError: If rate limit status cannot be fetched.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        response = await client.get(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        response.raise_for_status()

        data = response.json()
        core = data.get("resources", {}).get("core", {})

        return RateLimitInfo(
            limit=core.get("limit", 0),
            remaining=core.get("remaining", 0),
            reset=float(core.get("reset", 0)),
            used=core.get("used", 0),
        )

    except httpx.HTTPError as e:
        raise GitHubReviewError(f"Failed to get rate limit status: {e}") from e

    finally:
        if should_close_client:
            await client.aclose()


async def check_rate_limit_before_review(
    token: str,
    http_client: httpx.AsyncClient | None = None,
    min_remaining: int = 10,
) -> tuple[bool, RateLimitInfo]:
    """
    Check if there is sufficient rate limit capacity before posting a review.

    Args:
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.
        min_remaining: Minimum remaining requests required.

    Returns:
        Tuple of (can_proceed, rate_limit_info).
    """
    rate_limit = await get_rate_limit_status(token, http_client)
    can_proceed = rate_limit.remaining >= min_remaining
    return can_proceed, rate_limit
