"""Entry point for AI PR Reviewer."""

import logging
import sys

import uvicorn
from dotenv import load_dotenv

from .config import Config
from .github.auth import GitHubAuth
from .reviewer.queue import ReviewQueue
from .reviewer.worker import ReviewWorker
from .server import app, init_app


def main() -> None:
    """Run the AI PR Reviewer server."""
    # Load .env from current working directory
    load_dotenv(".env", override=False)

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Load configuration
    try:
        config = Config.from_env()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Initialize components
    github_auth = GitHubAuth(
        app_id=config.github_app_id,
        private_key=config.github_private_key,
    )
    review_queue = ReviewQueue()

    # Initialize FastAPI app
    init_app(config, github_auth, review_queue)

    # Create worker
    worker = ReviewWorker(
        queue=review_queue,
        config=config,
        github_auth=github_auth,
    )

    @app.on_event("startup")
    async def startup_event() -> None:
        """Start the review worker on app startup."""
        await worker.start()
        logger.info("AI PR Reviewer started")

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        """Stop the review worker on app shutdown."""
        await worker.stop()
        logger.info("AI PR Reviewer stopped")

    logger.info(f"Starting AI PR Reviewer on {config.host}:{config.port}")

    # Run server
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
