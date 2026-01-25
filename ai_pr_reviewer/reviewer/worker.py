"""Review worker that processes jobs from the queue."""

import asyncio
import logging
from typing import Protocol

from ..config import Config
from ..github.auth import GitHubAuth
from ..repo.manager import RepoManager
from .queue import ReviewJob, ReviewQueue
from .runner import build_mcp_servers, build_review_prompt, run_claude_review

logger = logging.getLogger(__name__)


class ReviewProcessor(Protocol):
    """Protocol for review processing."""

    async def process(self, job: ReviewJob, token: str) -> None:
        """Process a review job."""
        ...


class DefaultReviewProcessor:
    """Default review processor using Claude Code SDK."""

    def __init__(self, config: Config, repo_manager: RepoManager) -> None:
        """Initialize the processor."""
        self.config = config
        self.repo_manager = repo_manager

    async def process(self, job: ReviewJob, token: str) -> None:
        """Process a review job."""
        logger.info(
            f"Processing review for PR #{job.pr_number} in {job.owner}/{job.repo} "
            f"by reviewer '{job.reviewer_name}'"
        )

        # Clone the repository
        cloned_repo = await self.repo_manager.clone(
            owner=job.owner,
            repo=job.repo,
            head_ref=job.head_ref,
            head_sha=job.head_sha,
            token=token,
        )

        try:
            # Build the review prompt
            prompt = build_review_prompt(
                reviewer_name=job.reviewer_name,
                reviewer_prompt=job.reviewer_prompt,
                pr_title=job.pr_title,
                pr_body=job.pr_body,
                pr_number=job.pr_number,
                base_ref=job.base_ref,
                head_ref=job.head_ref,
                changed_files=job.changed_files,
            )

            # Build MCP servers config
            mcp_servers = build_mcp_servers(
                github_token=token,
                owner=job.owner,
                repo=job.repo,
                pr_number=job.pr_number,
                head_sha=job.head_sha,
            )

            # Run the review
            result = await run_claude_review(
                repo_path=cloned_repo.path,
                prompt=prompt,
                mcp_servers=mcp_servers,
                anthropic_api_key=self.config.anthropic_api_key,
            )

            if not result.success:
                raise RuntimeError(f"Claude Code failed: {result.stderr}")

            logger.info(f"Review completed for PR #{job.pr_number}")

        finally:
            # Clean up the cloned repo
            await self.repo_manager.cleanup(cloned_repo)


class ReviewWorker:
    """Worker that processes review jobs from the queue."""

    def __init__(
        self,
        queue: ReviewQueue,
        config: Config,
        github_auth: GitHubAuth,
        processor: ReviewProcessor | None = None,
    ) -> None:
        """Initialize the worker."""
        self.queue = queue
        self.config = config
        self.github_auth = github_auth
        self.repo_manager = RepoManager()
        self.processor = processor or DefaultReviewProcessor(
            config, self.repo_manager
        )
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the worker."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Review worker started")

    async def stop(self) -> None:
        """Stop the worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Review worker stopped")

    async def _run(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                # Get next job from queue
                job = await asyncio.wait_for(self.queue.dequeue(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                # Get installation token
                token = await self.github_auth.get_installation_token(
                    job.installation_id
                )

                # Process the job
                await self.processor.process(job, token)

                # Mark job as completed
                self.queue.complete(job.job_id)

            except Exception as e:
                logger.exception(f"Failed to process job {job.job_id}")
                self.queue.fail(job.job_id, str(e))
