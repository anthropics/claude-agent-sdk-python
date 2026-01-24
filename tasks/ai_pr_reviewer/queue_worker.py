"""Queue worker for processing PR reviews.

Provides a background worker that consumes from the review queue,
processes reviews sequentially, and handles retries for transient failures.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Protocol

from .review_queue import InMemoryReviewQueue, JobStatus, ReviewJob

logger = logging.getLogger(__name__)


class ReviewProcessorProtocol(Protocol):
    """Protocol for review processing implementations."""

    async def process_review(self, job: ReviewJob) -> None:
        """
        Process a review job.

        Args:
            job: The review job to process.

        Raises:
            Exception: If the review processing fails.
        """
        ...


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0

    def get_delay(self, attempt: int) -> float:
        """
        Calculate the delay for a given retry attempt.

        Uses exponential backoff with a maximum delay cap.

        Args:
            attempt: The retry attempt number (0-indexed).

        Returns:
            The delay in seconds before the next retry.
        """
        delay = self.initial_delay_seconds * (self.backoff_multiplier**attempt)
        return min(delay, self.max_delay_seconds)


class PRLockManager:
    """
    Manages locks to prevent concurrent reviews on the same PR.

    Ensures that only one review can be processed at a time for a given
    (repository, PR number) combination, regardless of which reviewer
    is performing the review.
    """

    def __init__(self) -> None:
        """Initialize the lock manager."""
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_counts: dict[str, int] = {}
        self._manager_lock = asyncio.Lock()

    def _get_pr_key(self, repository_full_name: str, pr_number: int) -> str:
        """Generate a unique key for a PR."""
        return f"{repository_full_name}#{pr_number}"

    async def acquire(self, repository_full_name: str, pr_number: int) -> None:
        """
        Acquire a lock for a PR.

        Blocks until the lock is acquired. Multiple reviewers for the same PR
        will be serialized.

        Args:
            repository_full_name: The full repository name (e.g., "owner/repo").
            pr_number: The pull request number.
        """
        pr_key = self._get_pr_key(repository_full_name, pr_number)

        # Get or create the lock for this PR
        async with self._manager_lock:
            if pr_key not in self._locks:
                self._locks[pr_key] = asyncio.Lock()
                self._lock_counts[pr_key] = 0
            self._lock_counts[pr_key] += 1

        # Acquire the PR-specific lock
        await self._locks[pr_key].acquire()

    async def release(self, repository_full_name: str, pr_number: int) -> None:
        """
        Release a lock for a PR.

        Args:
            repository_full_name: The full repository name (e.g., "owner/repo").
            pr_number: The pull request number.
        """
        pr_key = self._get_pr_key(repository_full_name, pr_number)

        async with self._manager_lock:
            if pr_key in self._locks:
                self._locks[pr_key].release()
                self._lock_counts[pr_key] -= 1

                # Clean up if no more waiters
                if self._lock_counts[pr_key] <= 0:
                    del self._locks[pr_key]
                    del self._lock_counts[pr_key]


class TransientError(Exception):
    """Exception indicating a transient/recoverable error that can be retried."""

    pass


class QueueWorker:
    """
    Background worker that processes review jobs from the queue.

    Processes jobs sequentially with PR-level locking to prevent
    concurrent reviews on the same PR. Includes retry logic for
    transient failures.
    """

    def __init__(
        self,
        queue: InMemoryReviewQueue,
        processor: ReviewProcessorProtocol,
        retry_config: RetryConfig | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        """
        Initialize the queue worker.

        Args:
            queue: The review queue to consume from.
            processor: The review processor implementation.
            retry_config: Configuration for retry behavior.
            poll_interval_seconds: How often to poll the queue when empty.
        """
        self._queue = queue
        self._processor = processor
        self._retry_config = retry_config or RetryConfig()
        self._poll_interval = poll_interval_seconds
        self._lock_manager = PRLockManager()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._retry_counts: dict[str, int] = {}

    @property
    def is_running(self) -> bool:
        """Check if the worker is currently running."""
        return self._running

    async def start(self) -> None:
        """
        Start the background worker.

        The worker will run in the background until stop() is called.
        """
        if self._running:
            logger.warning("Worker is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Queue worker started")

    async def stop(self) -> None:
        """
        Stop the background worker.

        Waits for the current job (if any) to complete before stopping.
        """
        if not self._running:
            return

        self._running = False

        if self._task is not None:
            # Wait for the task to complete
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        logger.info("Queue worker stopped")

    async def _run_loop(self) -> None:
        """Main worker loop that processes jobs from the queue."""
        while self._running:
            job = self._queue.dequeue()

            if job is None:
                # Queue is empty, wait before polling again
                await asyncio.sleep(self._poll_interval)
                continue

            await self._process_job(job)

    async def _process_job(self, job: ReviewJob) -> None:
        """
        Process a single job with locking and retry logic.

        Args:
            job: The job to process.
        """
        trigger = job.trigger

        logger.info(
            f"Processing job {job.job_id} for PR {trigger.pr_number} "
            f"in {trigger.repository_full_name}"
        )

        # Acquire lock for this PR
        await self._lock_manager.acquire(
            trigger.repository_full_name, trigger.pr_number
        )

        try:
            await self._process_with_retry(job)
            self._queue.mark_completed(job.job_id)
            # Clear retry count on success
            self._retry_counts.pop(job.job_id, None)
            logger.info(f"Job {job.job_id} completed successfully")
        except TransientError as e:
            # Handle transient error with retry
            await self._handle_transient_error(job, e)
        except Exception as e:
            # Non-transient error, fail immediately
            error_msg = f"Non-transient error: {type(e).__name__}: {e}"
            self._queue.mark_failed(job.job_id, error_msg)
            self._retry_counts.pop(job.job_id, None)
            logger.error(f"Job {job.job_id} failed: {error_msg}")
        finally:
            # Always release the lock
            await self._lock_manager.release(
                trigger.repository_full_name, trigger.pr_number
            )

    async def _process_with_retry(self, job: ReviewJob) -> None:
        """
        Attempt to process a job, raising TransientError for retryable failures.

        Args:
            job: The job to process.

        Raises:
            TransientError: If a transient error occurs.
            Exception: If a non-transient error occurs.
        """
        await self._processor.process_review(job)

    async def _handle_transient_error(
        self, job: ReviewJob, error: TransientError
    ) -> None:
        """
        Handle a transient error by retrying or failing the job.

        Args:
            job: The job that encountered the error.
            error: The transient error.
        """
        retry_count = self._retry_counts.get(job.job_id, 0)

        if retry_count < self._retry_config.max_retries:
            # Retry the job
            delay = self._retry_config.get_delay(retry_count)
            self._retry_counts[job.job_id] = retry_count + 1

            logger.warning(
                f"Job {job.job_id} encountered transient error (attempt "
                f"{retry_count + 1}/{self._retry_config.max_retries + 1}): {error}. "
                f"Retrying in {delay:.1f}s"
            )

            await asyncio.sleep(delay)

            # Re-enqueue the job for retry (it will get a new status)
            # We need to reset job status to allow re-processing
            job.status = JobStatus.QUEUED
            job.started_at = None

            # Add back to the front of the queue conceptually
            # Since we can't easily add to front of deque without modifying queue,
            # we'll just call the processor again directly
            await self._process_job(job)
        else:
            # Max retries exceeded
            error_msg = (
                f"Transient error after {retry_count + 1} attempts: "
                f"{type(error).__name__}: {error}"
            )
            self._queue.mark_failed(job.job_id, error_msg)
            self._retry_counts.pop(job.job_id, None)
            logger.error(f"Job {job.job_id} failed after max retries: {error_msg}")

    def get_retry_count(self, job_id: str) -> int:
        """
        Get the current retry count for a job.

        Args:
            job_id: The job ID.

        Returns:
            The number of retries attempted for this job.
        """
        return self._retry_counts.get(job_id, 0)
