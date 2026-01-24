"""GitHub webhook server for AI PR Reviewer."""

import hashlib
import hmac
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import AppConfig
from .github_auth import GitHubAppAuth
from .reviewer_config import (
    ConfigNotFoundError,
    ConfigParseError,
    fetch_reviewer_config,
)
from .trigger_detection import (
    TriggerDetectionResult,
    detect_review_triggers,
    is_review_requested_event,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="AI PR Reviewer", version="1.0.0")

# Global config - loaded at startup
_config: AppConfig | None = None
_github_auth: GitHubAppAuth | None = None


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

    Specifically looks for the review_requested action to trigger AI reviews.
    Fetches the repository's reviewer config and detects which configured
    AI reviewers were requested.
    """
    action = payload.get("action")

    if not is_review_requested_event(payload):
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

    # Return information about triggered reviews
    # (Actual queueing will be handled in a future user story)
    triggered_info = [
        {
            "reviewer": trigger.reviewer.username,
            "pr_number": trigger.pr_number,
            "pr_title": trigger.pr_title,
        }
        for trigger in result.triggered_reviews
    ]

    return {
        "status": "accepted",
        "action": action,
        "repository": repo_full_name,
        "triggered_reviews": triggered_info,
        "ignored_reviewers": result.ignored_reviewers,
    }
