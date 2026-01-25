"""Review job queue."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReviewJob:
    """A review job in the queue."""

    job_id: str
    owner: str
    repo: str
    pr_number: int
    pr_title: str
    pr_body: str
    head_ref: str
    head_sha: str
    base_ref: str
    installation_id: int
    reviewer_name: str
    reviewer_prompt: str
    changed_files: list[str]
    labels: list[str]
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    error: str | None = None


class ReviewQueue:
    """In-memory review job queue."""

    def __init__(self) -> None:
        """Initialize the queue."""
        self._queue: asyncio.Queue[ReviewJob] = asyncio.Queue()
        self._jobs: dict[str, ReviewJob] = {}

    def enqueue(self, job: ReviewJob) -> None:
        """Add a job to the queue."""
        # Check for duplicate
        if job.job_id in self._jobs:
            logger.info(f"Job {job.job_id} already exists, skipping")
            return

        self._jobs[job.job_id] = job
        self._queue.put_nowait(job)
        logger.info(f"Enqueued job {job.job_id}")

    async def dequeue(self) -> ReviewJob:
        """Get the next job from the queue."""
        job = await self._queue.get()
        job.status = JobStatus.PROCESSING
        return job

    def complete(self, job_id: str) -> None:
        """Mark a job as completed."""
        if job_id in self._jobs:
            self._jobs[job_id].status = JobStatus.COMPLETED
            logger.info(f"Job {job_id} completed")

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job as failed."""
        if job_id in self._jobs:
            self._jobs[job_id].status = JobStatus.FAILED
            self._jobs[job_id].error = error
            logger.error(f"Job {job_id} failed: {error}")

    def get_job(self, job_id: str) -> ReviewJob | None:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[ReviewJob]:
        """Get all jobs."""
        return list(self._jobs.values())

    def queue_size(self) -> int:
        """Get the number of pending jobs."""
        return self._queue.qsize()


def create_job_id(owner: str, repo: str, pr_number: int, reviewer_name: str) -> str:
    """Create a unique job ID."""
    return f"{owner}/{repo}#{pr_number}#{reviewer_name}"
