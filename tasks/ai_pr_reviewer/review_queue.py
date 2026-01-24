"""Review queue implementation for AI PR Reviewer.

Provides a queue system for processing PR reviews with FIFO ordering,
duplicate detection, and job status tracking.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from .trigger_detection import ReviewTrigger


class JobStatus(Enum):
    """Status of a review job in the queue."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReviewJob:
    """
    A queued review job.

    Represents a single review request that has been queued for processing.
    """

    job_id: str
    trigger: ReviewTrigger
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


def generate_job_id(
    pr_number: int, repository_full_name: str, reviewer_username: str
) -> str:
    """
    Generate a unique job ID for a (PR, reviewer) combination.

    The job ID uniquely identifies a review request and is used for
    duplicate detection. Multiple requests for the same PR by the same
    reviewer will generate the same job ID.

    Args:
        pr_number: The pull request number.
        repository_full_name: The full repository name (e.g., "owner/repo").
        reviewer_username: The GitHub username of the reviewer.

    Returns:
        A unique job ID string.
    """
    return f"{repository_full_name}#{pr_number}#{reviewer_username}"


class ReviewQueueProtocol(Protocol):
    """Protocol for review queue implementations."""

    def enqueue(self, trigger: ReviewTrigger) -> ReviewJob | None:
        """
        Add a review trigger to the queue.

        Returns the created job, or None if a duplicate already exists.
        """
        ...

    def dequeue(self) -> ReviewJob | None:
        """
        Get the next job from the queue and mark it as in_progress.

        Returns the job, or None if the queue is empty.
        """
        ...

    def get_job(self, job_id: str) -> ReviewJob | None:
        """Get a job by its ID."""
        ...

    def mark_completed(self, job_id: str) -> bool:
        """Mark a job as completed. Returns True if successful."""
        ...

    def mark_failed(self, job_id: str, error_message: str) -> bool:
        """Mark a job as failed. Returns True if successful."""
        ...

    def is_duplicate(self, job_id: str) -> bool:
        """Check if a job with this ID is already queued or in progress."""
        ...

    def queue_size(self) -> int:
        """Get the number of queued jobs."""
        ...

    def get_all_jobs(self) -> list[ReviewJob]:
        """Get all jobs (for debugging/monitoring)."""
        ...


class InMemoryReviewQueue:
    """
    In-memory implementation of the review queue.

    Provides FIFO ordering, duplicate detection, and job status tracking.
    Thread-safety is not guaranteed - use with asyncio or external locking.
    """

    def __init__(self) -> None:
        """Initialize an empty queue."""
        # FIFO queue of job IDs
        self._queue: deque[str] = deque()
        # All jobs indexed by job_id
        self._jobs: dict[str, ReviewJob] = {}

    def enqueue(self, trigger: ReviewTrigger) -> ReviewJob | None:
        """
        Add a review trigger to the queue.

        Creates a new review job from the trigger and adds it to the queue.
        If a job with the same (PR, reviewer) combination already exists
        and is queued or in_progress, the request is skipped (duplicate).

        Args:
            trigger: The review trigger containing PR and reviewer info.

        Returns:
            The created ReviewJob, or None if a duplicate exists.
        """
        job_id = generate_job_id(
            pr_number=trigger.pr_number,
            repository_full_name=trigger.repository_full_name,
            reviewer_username=trigger.reviewer.username,
        )

        # Check for duplicates - skip if already queued or in_progress
        if self.is_duplicate(job_id):
            return None

        job = ReviewJob(
            job_id=job_id,
            trigger=trigger,
            status=JobStatus.QUEUED,
        )

        self._jobs[job_id] = job
        self._queue.append(job_id)

        return job

    def dequeue(self) -> ReviewJob | None:
        """
        Get the next job from the queue and mark it as in_progress.

        Removes the job from the FIFO queue and updates its status.

        Returns:
            The next ReviewJob to process, or None if the queue is empty.
        """
        if not self._queue:
            return None

        job_id = self._queue.popleft()
        job = self._jobs.get(job_id)

        if job is None:
            # This shouldn't happen, but handle gracefully
            return self.dequeue()

        job.status = JobStatus.IN_PROGRESS
        job.started_at = datetime.now(timezone.utc)

        return job

    def get_job(self, job_id: str) -> ReviewJob | None:
        """
        Get a job by its ID.

        Args:
            job_id: The unique job identifier.

        Returns:
            The ReviewJob if found, None otherwise.
        """
        return self._jobs.get(job_id)

    def mark_completed(self, job_id: str) -> bool:
        """
        Mark a job as completed.

        Args:
            job_id: The unique job identifier.

        Returns:
            True if the job was found and marked completed, False otherwise.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False

        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        return True

    def mark_failed(self, job_id: str, error_message: str) -> bool:
        """
        Mark a job as failed.

        Args:
            job_id: The unique job identifier.
            error_message: Description of the failure.

        Returns:
            True if the job was found and marked failed, False otherwise.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False

        job.status = JobStatus.FAILED
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = error_message
        return True

    def is_duplicate(self, job_id: str) -> bool:
        """
        Check if a job with this ID is already queued or in progress.

        Completed and failed jobs are not considered duplicates,
        allowing the same (PR, reviewer) to be requeued after completion.

        Args:
            job_id: The unique job identifier.

        Returns:
            True if a duplicate exists (queued or in_progress), False otherwise.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False

        return job.status in (JobStatus.QUEUED, JobStatus.IN_PROGRESS)

    def queue_size(self) -> int:
        """
        Get the number of queued jobs.

        Returns:
            The number of jobs waiting to be processed.
        """
        return len(self._queue)

    def get_all_jobs(self) -> list[ReviewJob]:
        """
        Get all jobs in the system.

        Useful for debugging and monitoring.

        Returns:
            A list of all ReviewJob objects.
        """
        return list(self._jobs.values())

    def get_jobs_by_status(self, status: JobStatus) -> list[ReviewJob]:
        """
        Get all jobs with a specific status.

        Args:
            status: The job status to filter by.

        Returns:
            A list of ReviewJob objects with the given status.
        """
        return [job for job in self._jobs.values() if job.status == status]

    def clear_completed(self) -> int:
        """
        Remove all completed and failed jobs from the job store.

        Useful for cleaning up old jobs to prevent memory growth.

        Returns:
            The number of jobs removed.
        """
        to_remove = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]

        for job_id in to_remove:
            del self._jobs[job_id]

        return len(to_remove)
