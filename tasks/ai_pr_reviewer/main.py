"""Main entry point for AI PR Reviewer.

Starts the FastAPI webhook server with background queue worker.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from .claude_integration import generate_review
from .github_auth import GitHubAppAuth
from .github_review import post_review
from .inline_comments import map_inline_comments
from .pr_context import fetch_pr_context
from .queue_worker import QueueWorker
from .review_queue import InMemoryReviewQueue, ReviewJob
from .review_summary import generate_review_summary
from .review_tracker import InMemoryReviewTracker
from .webhook import app as webhook_app
from .webhook import get_config, get_github_auth, set_review_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class FullReviewProcessor:
    """
    Complete review processor that generates and posts reviews.

    Implements ReviewProcessorProtocol for use with QueueWorker.
    """

    def __init__(self, github_auth: GitHubAppAuth) -> None:
        """
        Initialize the processor.

        Args:
            github_auth: GitHub App authentication handler.
        """
        self._github_auth = github_auth

    async def process_review(self, job: ReviewJob) -> None:
        """
        Process a review job: fetch context, generate review, and post to GitHub.

        Args:
            job: The review job to process.

        Raises:
            Exception: If any step of the review process fails.
        """
        trigger = job.trigger

        logger.info(
            f"Processing review for PR #{trigger.pr_number} "
            f"in {trigger.repository_full_name} "
            f"by reviewer '{trigger.reviewer.username}'"
        )

        # Get installation token
        token = await self._github_auth.get_installation_token(trigger.installation_id)

        # Fetch PR context
        context = await fetch_pr_context(
            owner=trigger.repository_owner,
            repo=trigger.repository_name,
            pr_number=trigger.pr_number,
            token=token,
        )

        logger.info(
            f"Fetched PR context: {len(context.files)} files, "
            f"{len(context.commits)} commits"
        )

        # Generate review using Claude
        review_output = await generate_review(
            context=context,
            reviewer_settings=trigger.reviewer,
        )

        logger.info(
            f"Generated review: assessment={review_output.overall_assessment}, "
            f"findings={len(review_output.key_findings)}, "
            f"inline_comments={len(review_output.inline_comments)}"
        )

        # Generate review summary
        summary = generate_review_summary(
            review_output=review_output,
            reviewer_username=trigger.reviewer.username,
        )

        # Map inline comments to file positions
        mapped_comments = map_inline_comments(
            comments=review_output.inline_comments,
            files=context.files,
        )

        # Post review to GitHub
        posted_review = await post_review(
            owner=trigger.repository_owner,
            repo=trigger.repository_name,
            pr_number=trigger.pr_number,
            commit_id=trigger.head_sha,
            summary=summary,
            inline_comments=mapped_comments,
            token=token,
        )

        logger.info(
            f"Posted review #{posted_review.review_id} to PR #{trigger.pr_number}: "
            f"{posted_review.html_url}"
        )


class TokenProvider:
    """Provides GitHub access tokens for installations."""

    def __init__(self, github_auth: GitHubAppAuth) -> None:
        self._github_auth = github_auth

    async def get_token(self, installation_id: int) -> str:
        return await self._github_auth.get_installation_token(installation_id)


# Global worker instance
_worker: QueueWorker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Starts the queue worker on startup and stops it on shutdown.
    """
    global _worker

    # Initialize shared components
    github_auth = get_github_auth()
    review_queue = InMemoryReviewQueue()
    review_tracker = InMemoryReviewTracker()

    # Set the queue in the webhook module
    set_review_queue(review_queue)

    # Create the review processor
    processor = FullReviewProcessor(github_auth)
    token_provider = TokenProvider(github_auth)

    # Create and start the worker
    _worker = QueueWorker(
        queue=review_queue,
        processor=processor,
        review_tracker=review_tracker,
        token_provider=token_provider,
        poll_interval_seconds=1.0,
    )

    await _worker.start()
    logger.info("AI PR Reviewer started")

    yield

    # Shutdown
    if _worker is not None:
        await _worker.stop()
    logger.info("AI PR Reviewer stopped")


# Create the main app with lifespan
app = FastAPI(
    title="AI PR Reviewer",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount the webhook routes
app.mount("/", webhook_app)


def main() -> None:
    """Run the AI PR Reviewer server."""
    # Load .env file from the ai_pr_reviewer directory
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)

    config = get_config()

    if not config.github_app_id:
        logger.warning(
            "GITHUB_APP_ID not set - running in local test mode "
            "(GitHub API calls will fail)"
        )

    if not config.github_private_key:
        logger.warning(
            "GITHUB_PRIVATE_KEY not set - running in local test mode "
            "(GitHub API calls will fail)"
        )

    logger.info("Starting AI PR Reviewer server on http://0.0.0.0:8000")
    uvicorn.run(
        "tasks.ai_pr_reviewer.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
