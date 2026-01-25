"""FastAPI server for AI PR Reviewer."""

import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import Config
from .github.api import GitHubAPI
from .github.auth import GitHubAuth
from .reviewer.config import load_reviewer_config
from .reviewer.queue import ReviewJob, ReviewQueue, create_job_id

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI PR Reviewer",
    description="GitHub App for AI-powered PR reviews using Claude Code",
    version="0.1.0",
)

# Global dependencies - set at startup
_config: Config | None = None
_github_auth: GitHubAuth | None = None
_review_queue: ReviewQueue | None = None


def init_app(
    config: Config,
    github_auth: GitHubAuth,
    review_queue: ReviewQueue,
) -> None:
    """Initialize the application with dependencies."""
    global _config, _github_auth, _review_queue
    _config = config
    _github_auth = github_auth
    _review_queue = review_queue


def get_config() -> Config:
    """Get the application configuration."""
    if _config is None:
        raise RuntimeError("Application not initialized")
    return _config


def get_github_auth() -> GitHubAuth:
    """Get the GitHub auth handler."""
    if _github_auth is None:
        raise RuntimeError("Application not initialized")
    return _github_auth


def get_review_queue() -> ReviewQueue:
    """Get the review queue."""
    if _review_queue is None:
        raise RuntimeError("Application not initialized")
    return _review_queue


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True  # Skip in dev mode

    if not signature or not signature.startswith("sha256="):
        return False

    expected = signature[7:]
    computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/queue/status")
async def queue_status() -> dict[str, Any]:
    """Get queue status."""
    queue = get_review_queue()
    jobs = queue.get_all_jobs()
    return {
        "queue_size": queue.queue_size(),
        "total_jobs": len(jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "status": job.status.value,
                "reviewer": job.reviewer_name,
                "pr_number": job.pr_number,
                "repo": f"{job.owner}/{job.repo}",
            }
            for job in jobs
        ],
    }


@app.post("/webhook")
async def webhook_handler(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
) -> dict[str, Any]:
    """Handle incoming GitHub webhook events."""
    config = get_config()

    body = await request.body()
    if not verify_webhook_signature(
        body, x_hub_signature_256 or "", config.github_webhook_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        payload: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from e

    logger.info(f"Received webhook event: {x_github_event}")

    if x_github_event == "pull_request":
        return await handle_pull_request(payload)

    return {"status": "ignored", "event": x_github_event}


async def handle_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle pull_request webhook events."""
    action = payload.get("action")

    if action not in ("opened", "synchronize"):
        return {"status": "ignored", "action": action}

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    pr_number = pr.get("number")
    pr_title = pr.get("title", "")
    pr_body = pr.get("body", "")
    head_ref = pr.get("head", {}).get("ref", "")
    head_sha = pr.get("head", {}).get("sha", "")
    base_ref = pr.get("base", {}).get("ref", "")
    installation_id = installation.get("id")
    labels = [label.get("name", "") for label in pr.get("labels", [])]

    logger.info(f"Processing PR #{pr_number} in {owner}/{repo_name}")

    # Get installation token
    github_auth = get_github_auth()
    try:
        token = await github_auth.get_installation_token(installation_id)
    except Exception as e:
        logger.error(f"Failed to get installation token: {e}")
        return {"status": "error", "error": "Authentication failed"}

    # Fetch .ai-reviewer.yml config
    api = GitHubAPI(token)
    config_content = await api.get_file_content(
        owner, repo_name, ".ai-reviewer.yml", head_sha
    )

    if not config_content:
        logger.info(f"No .ai-reviewer.yml in {owner}/{repo_name}")
        return {"status": "ignored", "reason": "no_config"}

    try:
        reviewer_config = load_reviewer_config(config_content)
    except ValueError as e:
        logger.warning(f"Invalid .ai-reviewer.yml: {e}")
        return {"status": "error", "error": str(e)}

    # Get changed files
    changed_files_data = await api.get_pull_request_files(owner, repo_name, pr_number)
    changed_files = [f.get("filename", "") for f in changed_files_data]

    # Find triggered reviewers
    triggered = reviewer_config.get_triggered_reviewers(changed_files, labels)

    if not triggered:
        logger.info("No reviewers triggered")
        return {"status": "ignored", "reason": "no_reviewers_triggered"}

    # Queue review jobs
    queue = get_review_queue()
    queued_jobs = []

    for reviewer in triggered:
        job_id = create_job_id(owner, repo_name, pr_number, reviewer.name)
        job = ReviewJob(
            job_id=job_id,
            owner=owner,
            repo=repo_name,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_body=pr_body,
            head_ref=head_ref,
            head_sha=head_sha,
            base_ref=base_ref,
            installation_id=installation_id,
            reviewer_name=reviewer.name,
            reviewer_prompt=reviewer.prompt,
            changed_files=changed_files,
            labels=labels,
        )
        queue.enqueue(job)
        queued_jobs.append({"job_id": job_id, "reviewer": reviewer.name})
        logger.info(f"Queued review by '{reviewer.name}' for PR #{pr_number}")

    return {
        "status": "accepted",
        "action": action,
        "pr_number": pr_number,
        "repository": f"{owner}/{repo_name}",
        "queued_jobs": queued_jobs,
    }
