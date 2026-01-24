"""Review deduplication tracker for AI PR Reviewer.

Tracks completed reviews per (PR, reviewer, commit SHA) combination
to prevent duplicate reviews when the same commit is reviewed multiple times.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class CompletedReview:
    """
    Record of a completed review.

    Tracks when a specific reviewer completed a review for a PR at a given commit.
    """

    repository_full_name: str
    pr_number: int
    reviewer_username: str
    commit_sha: str
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def generate_review_key(
    repository_full_name: str,
    pr_number: int,
    reviewer_username: str,
    commit_sha: str,
) -> str:
    """
    Generate a unique key for a (PR, reviewer, commit) combination.

    Args:
        repository_full_name: The full repository name (e.g., "owner/repo").
        pr_number: The pull request number.
        reviewer_username: The GitHub username of the reviewer.
        commit_sha: The commit SHA at which the review was performed.

    Returns:
        A unique key string.
    """
    return f"{repository_full_name}#{pr_number}#{reviewer_username}#{commit_sha}"


class ReviewTrackerProtocol(Protocol):
    """Protocol for review tracker implementations."""

    def has_reviewed(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> bool:
        """
        Check if a review has already been completed for this combination.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA to check.

        Returns:
            True if already reviewed, False otherwise.
        """
        ...

    def mark_reviewed(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> CompletedReview:
        """
        Mark a review as completed.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA that was reviewed.

        Returns:
            The created CompletedReview record.
        """
        ...

    def get_review(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> CompletedReview | None:
        """
        Get a completed review record if it exists.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA.

        Returns:
            The CompletedReview if found, None otherwise.
        """
        ...


class InMemoryReviewTracker:
    """
    In-memory implementation of the review tracker.

    Tracks completed reviews to prevent duplicate reviews on the same commit.
    Note: Data is lost on restart. For production, consider a persistent store.
    """

    def __init__(self) -> None:
        """Initialize an empty tracker."""
        self._reviews: dict[str, CompletedReview] = {}

    def has_reviewed(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> bool:
        """
        Check if a review has already been completed for this combination.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA to check.

        Returns:
            True if already reviewed, False otherwise.
        """
        key = generate_review_key(
            repository_full_name, pr_number, reviewer_username, commit_sha
        )
        return key in self._reviews

    def mark_reviewed(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> CompletedReview:
        """
        Mark a review as completed.

        Creates a record of the completed review for deduplication purposes.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA that was reviewed.

        Returns:
            The created CompletedReview record.
        """
        key = generate_review_key(
            repository_full_name, pr_number, reviewer_username, commit_sha
        )

        review = CompletedReview(
            repository_full_name=repository_full_name,
            pr_number=pr_number,
            reviewer_username=reviewer_username,
            commit_sha=commit_sha,
        )

        self._reviews[key] = review
        return review

    def get_review(
        self,
        repository_full_name: str,
        pr_number: int,
        reviewer_username: str,
        commit_sha: str,
    ) -> CompletedReview | None:
        """
        Get a completed review record if it exists.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.
            reviewer_username: The reviewer's GitHub username.
            commit_sha: The commit SHA.

        Returns:
            The CompletedReview if found, None otherwise.
        """
        key = generate_review_key(
            repository_full_name, pr_number, reviewer_username, commit_sha
        )
        return self._reviews.get(key)

    def get_reviews_for_pr(
        self,
        repository_full_name: str,
        pr_number: int,
    ) -> list[CompletedReview]:
        """
        Get all completed reviews for a specific PR.

        Useful for listing review history for a PR across all reviewers and commits.

        Args:
            repository_full_name: The full repository name.
            pr_number: The pull request number.

        Returns:
            List of CompletedReview records for this PR.
        """
        prefix = f"{repository_full_name}#{pr_number}#"
        return [
            review for key, review in self._reviews.items() if key.startswith(prefix)
        ]

    def clear_old_reviews(self, older_than: datetime) -> int:
        """
        Remove reviews completed before a given datetime.

        Helps manage memory by cleaning up old review records.

        Args:
            older_than: Remove reviews completed before this time.

        Returns:
            The number of reviews removed.
        """
        to_remove = [
            key
            for key, review in self._reviews.items()
            if review.completed_at < older_than
        ]

        for key in to_remove:
            del self._reviews[key]

        return len(to_remove)

    def count(self) -> int:
        """
        Get the total number of tracked reviews.

        Returns:
            The number of review records in the tracker.
        """
        return len(self._reviews)
