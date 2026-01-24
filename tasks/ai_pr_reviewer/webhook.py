"""GitHub webhook server for AI PR Reviewer."""

import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import AppConfig
from .github_auth import GitHubAppAuth
from .review_queue import InMemoryReviewQueue
from .reviewer_config import (
    ConfigNotFoundError,
    ConfigParseError,
    RepoReviewerConfig,
    fetch_reviewer_config,
)
from .trigger_detection import (
    TriggerDetectionResult,
    create_auto_review_trigger,
    detect_review_triggers,
    is_pr_opened_event,
    is_review_requested_event,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="AI PR Reviewer", version="1.0.0")

# Global config - loaded at startup
_config: AppConfig | None = None
_github_auth: GitHubAppAuth | None = None
_review_queue: InMemoryReviewQueue | None = None


def get_review_queue() -> InMemoryReviewQueue:
    """Get the global review queue instance."""
    global _review_queue
    if _review_queue is None:
        _review_queue = InMemoryReviewQueue()
    return _review_queue


def set_review_queue(queue: InMemoryReviewQueue) -> None:
    """Set the global review queue instance (for dependency injection)."""
    global _review_queue
    _review_queue = queue


def get_config() -> AppConfig:
    """Get the application configuration."""
    global _config
    if _config is None:
        _config = AppConfig.from_env()
    return _config


def get_github_auth() -> GitHubAppAuth:
    """Get the GitHub App authentication handler."""
    global _github_auth
    if _github_auth is None:
        config = get_config()
        _github_auth = GitHubAppAuth(
            app_id=config.github_app_id,
            private_key=config.github_private_key,
        )
    return _github_auth


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify GitHub webhook HMAC-SHA256 signature.

    Args:
        payload: Raw request body bytes
        signature: The X-Hub-Signature-256 header value
        secret: The webhook secret configured in GitHub

    Returns:
        True if signature is valid, False otherwise
    """
    if not secret:
        # If no secret configured, skip verification (development mode)
        return True

    if not signature:
        return False

    # GitHub signature format: sha256=<hex_digest>
    if not signature.startswith("sha256="):
        return False

    expected_signature = signature[7:]  # Remove "sha256=" prefix

    # Compute HMAC-SHA256
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    computed_signature = mac.hexdigest()

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_signature, expected_signature)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/queue/status")
async def queue_status() -> dict[str, Any]:
    """Get current queue status (for debugging)."""
    queue = get_review_queue()
    jobs = queue.get_all_jobs()
    return {
        "queue_size": queue.queue_size(),
        "total_jobs": len(jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "status": job.status.value,
                "reviewer": job.trigger.reviewer.username,
                "pr_number": job.trigger.pr_number,
                "created_at": job.created_at.isoformat(),
            }
            for job in jobs
        ],
    }


@app.post("/test/enqueue")
async def test_enqueue(
    pr_number: int = 1,
    reviewer: str = "test-ai",
    repo: str = "dingkwang/test",
) -> dict[str, Any]:
    """
    Test endpoint to enqueue a mock review job.

    This bypasses GitHub API calls for local testing.
    Usage: curl -X POST "http://localhost:8000/test/enqueue?pr_number=1&reviewer=test-ai"
    """
    from .reviewer_config import ReviewerSettings
    from .trigger_detection import ReviewTrigger

    # Create mock reviewer settings
    mock_reviewer = ReviewerSettings(
        username=reviewer,
        prompt="Review this PR for code quality and best practices.",
        language="en",
    )

    # Create mock trigger
    owner, repo_name = repo.split("/") if "/" in repo else ("test", repo)
    mock_trigger = ReviewTrigger(
        pr_number=pr_number,
        pr_title=f"Test PR #{pr_number}",
        pr_url=f"https://github.com/{repo}/pull/{pr_number}",
        head_sha="abc123def456",
        base_ref="main",
        head_ref="feature-branch",
        repository_owner=owner,
        repository_name=repo_name,
        repository_full_name=repo,
        installation_id=0,  # Mock installation
        reviewer=mock_reviewer,
    )

    # Enqueue the job
    queue = get_review_queue()
    job = queue.enqueue(mock_trigger)

    if job is None:
        return {
            "status": "skipped",
            "reason": "duplicate",
            "message": f"Review for PR #{pr_number} by {reviewer} already queued",
        }

    return {
        "status": "queued",
        "job_id": job.job_id,
        "pr_number": pr_number,
        "reviewer": reviewer,
        "queue_size": queue.queue_size(),
    }


@app.post("/webhook")
async def webhook_handler(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
) -> dict[str, Any]:
    """
    Handle incoming GitHub webhook events.

    Validates the webhook signature and processes pull_request events
    with the review_requested action.
    """
    config = get_config()

    # Read raw body for signature verification
    body = await request.body()

    # Verify webhook signature
    if not verify_webhook_signature(
        body, x_hub_signature_256 or "", config.github_webhook_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Parse JSON payload
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from e

    # Handle different event types
    if x_github_event == "pull_request":
        return await handle_pull_request_event(payload)

    # For other events, acknowledge but don't process
    return {"status": "ignored", "event": x_github_event}


async def handle_pull_request_event(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Handle pull_request webhook events.

    Supports two trigger modes:
    1. review_requested: Trigger when specific AI reviewers are requested
    2. opened/synchronize: Auto-review on PR creation or update (if configured)
    """
    action = payload.get("action")
    is_review_request = is_review_requested_event(payload)
    is_pr_open = is_pr_opened_event(payload)

    if not is_review_request and not is_pr_open:
        # Other pull_request actions are acknowledged but not processed
        return {"status": "ignored", "action": action}

    # Extract repository and installation info for fetching config
    repository = payload.get("repository", {})
    installation = payload.get("installation", {})
    pr = payload.get("pull_request", {})

    repo_owner: str = repository.get("owner", {}).get("login", "")
    repo_name: str = repository.get("name", "")
    repo_full_name: str = repository.get("full_name", "")
    installation_id: int = installation.get("id", 0)
    head_sha: str = pr.get("head", {}).get("sha", "")

    # Get GitHub access token for this installation
    github_auth = get_github_auth()
    try:
        token = await github_auth.get_installation_token(installation_id)
    except Exception as e:
        logger.error(f"Failed to get installation token: {e}")
        return {
            "status": "error",
            "action": action,
            "error": "Failed to authenticate with GitHub",
        }

    # Fetch repository's reviewer configuration
    try:
        config = await fetch_reviewer_config(
            owner=repo_owner,
            repo=repo_name,
            ref=head_sha,
            token=token,
        )
    except ConfigNotFoundError:
        # No config file means no AI reviewers configured
        logger.info(f"No .ai-reviewer.yml found in {repo_full_name}")
        return {
            "status": "ignored",
            "action": action,
            "reason": "no_config",
            "repository": repo_full_name,
        }
    except ConfigParseError as e:
        logger.warning(f"Invalid .ai-reviewer.yml in {repo_full_name}: {e}")
        return {
            "status": "error",
            "action": action,
            "error": f"Invalid reviewer configuration: {e}",
        }

    # Handle based on trigger type
    if is_pr_open and config.auto_review:
        # Auto-review on PR open/synchronize
        return await handle_auto_review(payload, config, repo_full_name, action)

    if is_review_request:
        # Detect which AI reviewers were requested
        result: TriggerDetectionResult = detect_review_triggers(payload, config)

        if not result.has_triggers:
            # No configured AI reviewers were requested
            return {
                "status": "ignored",
                "action": action,
                "reason": "no_ai_reviewers_requested",
                "ignored_reviewers": result.ignored_reviewers,
                "repository": repo_full_name,
            }

        # Enqueue the triggered reviews
        return await enqueue_triggered_reviews(result, repo_full_name, action)

    return {"status": "ignored", "action": action, "reason": "no_trigger_match"}


async def handle_auto_review(
    payload: dict[str, Any],
    config: RepoReviewerConfig,
    repo_full_name: str,
    action: str | None,
) -> dict[str, Any]:
    """Handle auto-review on PR open/synchronize."""
    # Get the default reviewer for auto-review
    default_reviewer = config.get_default_reviewer()
    if default_reviewer is None:
        return {
            "status": "ignored",
            "action": action,
            "reason": "no_default_reviewer_for_auto_review",
            "repository": repo_full_name,
        }

    # Create trigger for auto-review
    trigger = create_auto_review_trigger(payload, default_reviewer)

    # Enqueue the review
    queue = get_review_queue()
    job = queue.enqueue(trigger)

    if job is None:
        return {
            "status": "skipped",
            "action": action,
            "reason": "duplicate",
            "repository": repo_full_name,
        }

    logger.info(
        f"Queued auto-review job {job.job_id} for PR #{trigger.pr_number} "
        f"in {repo_full_name}"
    )

    return {
        "status": "accepted",
        "action": action,
        "trigger_type": "auto_review",
        "repository": repo_full_name,
        "job_id": job.job_id,
        "pr_number": trigger.pr_number,
        "queue_size": queue.queue_size(),
    }


async def enqueue_triggered_reviews(
    result: TriggerDetectionResult,
    repo_full_name: str,
    action: str | None,
) -> dict[str, Any]:
    """Enqueue reviews from detected triggers."""
    queue = get_review_queue()
    queued_jobs: list[dict[str, Any]] = []
    skipped_duplicates: list[str] = []

    for trigger in result.triggered_reviews:
        job = queue.enqueue(trigger)
        if job is not None:
            queued_jobs.append(
                {
                    "job_id": job.job_id,
                    "reviewer": trigger.reviewer.username,
                    "pr_number": trigger.pr_number,
                    "pr_title": trigger.pr_title,
                }
            )
            logger.info(
                f"Queued review job {job.job_id} for PR #{trigger.pr_number} "
                f"by reviewer '{trigger.reviewer.username}'"
            )
        else:
            skipped_duplicates.append(trigger.reviewer.username)
            logger.info(
                f"Skipped duplicate review for PR #{trigger.pr_number} "
                f"by reviewer '{trigger.reviewer.username}'"
            )

    return {
        "status": "accepted",
        "action": action,
        "repository": repo_full_name,
        "queued_reviews": queued_jobs,
        "skipped_duplicates": skipped_duplicates,
        "ignored_reviewers": result.ignored_reviewers,
        "queue_size": queue.queue_size(),
    }
