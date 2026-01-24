"""Review trigger detection for AI PR Reviewer.

Detects when AI reviewers are requested for pull request reviews
and determines which configured reviewers should be triggered.
"""

from dataclasses import dataclass
from typing import Any

from .reviewer_config import RepoReviewerConfig, ReviewerSettings


@dataclass
class ReviewTrigger:
    """
    Represents a triggered AI review request.

    Contains all the information needed to queue and process a review.
    """

    pr_number: int
    pr_title: str
    pr_url: str
    head_sha: str
    base_ref: str
    head_ref: str
    repository_owner: str
    repository_name: str
    repository_full_name: str
    installation_id: int
    reviewer: ReviewerSettings


@dataclass
class TriggerDetectionResult:
    """Result of analyzing a webhook payload for review triggers."""

    triggered_reviews: list[ReviewTrigger]
    ignored_reviewers: list[str]

    @property
    def has_triggers(self) -> bool:
        """Check if any AI reviews were triggered."""
        return len(self.triggered_reviews) > 0


def detect_review_triggers(
    payload: dict[str, Any],
    config: RepoReviewerConfig,
) -> TriggerDetectionResult:
    """
    Detect if a webhook payload should trigger AI reviews.

    Analyzes a pull_request webhook event with action='review_requested'
    to determine if any configured AI reviewers were requested.

    Args:
        payload: The GitHub webhook payload (must be a pull_request event).
        config: The repository's reviewer configuration.

    Returns:
        A TriggerDetectionResult containing triggered reviews and ignored reviewers.
    """
    triggered_reviews: list[ReviewTrigger] = []
    ignored_reviewers: list[str] = []

    # Extract PR information
    pr = payload.get("pull_request", {})
    repository = payload.get("repository", {})
    installation = payload.get("installation", {})

    pr_number: int = pr.get("number", 0)
    pr_title: str = pr.get("title", "")
    pr_url: str = pr.get("html_url", "")
    head_sha: str = pr.get("head", {}).get("sha", "")
    base_ref: str = pr.get("base", {}).get("ref", "")
    head_ref: str = pr.get("head", {}).get("ref", "")

    repo_owner: str = repository.get("owner", {}).get("login", "")
    repo_name: str = repository.get("name", "")
    repo_full_name: str = repository.get("full_name", "")
    installation_id: int = installation.get("id", 0)

    # Get requested reviewers from the payload
    # Single reviewer request: "requested_reviewer" field
    # Multiple reviewers: need to check "pull_request.requested_reviewers" field
    requested_usernames = _extract_requested_reviewers(payload)

    for username in requested_usernames:
        if config.is_ai_reviewer(username):
            reviewer_settings = config.get_reviewer(username)
            if reviewer_settings is not None:
                trigger = ReviewTrigger(
                    pr_number=pr_number,
                    pr_title=pr_title,
                    pr_url=pr_url,
                    head_sha=head_sha,
                    base_ref=base_ref,
                    head_ref=head_ref,
                    repository_owner=repo_owner,
                    repository_name=repo_name,
                    repository_full_name=repo_full_name,
                    installation_id=installation_id,
                    reviewer=reviewer_settings,
                )
                triggered_reviews.append(trigger)
        else:
            ignored_reviewers.append(username)

    return TriggerDetectionResult(
        triggered_reviews=triggered_reviews,
        ignored_reviewers=ignored_reviewers,
    )


def _extract_requested_reviewers(payload: dict[str, Any]) -> list[str]:
    """
    Extract requested reviewer usernames from a webhook payload.

    GitHub's review_requested event can include:
    - A single "requested_reviewer" object for individual requests
    - A "requested_team" object for team requests (not handled for AI reviewers)

    Args:
        payload: The GitHub webhook payload.

    Returns:
        A list of requested reviewer usernames.
    """
    usernames: list[str] = []

    # Single reviewer request (from the event)
    requested_reviewer = payload.get("requested_reviewer")
    if requested_reviewer and isinstance(requested_reviewer, dict):
        login = requested_reviewer.get("login")
        if login and isinstance(login, str):
            usernames.append(login)

    return usernames


def is_review_requested_event(payload: dict[str, Any]) -> bool:
    """
    Check if a payload is a pull_request review_requested event.

    Args:
        payload: The GitHub webhook payload.

    Returns:
        True if this is a review_requested action, False otherwise.
    """
    action = payload.get("action")
    return action == "review_requested"
