"""GitHub webhook server for AI PR Reviewer."""

import hashlib
import hmac
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status

from .config import AppConfig

app = FastAPI(title="AI PR Reviewer", version="1.0.0")

# Global config - loaded at startup
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the application configuration."""
    global _config
    if _config is None:
        _config = AppConfig.from_env()
    return _config


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
    """
    action = payload.get("action")

    if action == "review_requested":
        # Extract relevant information
        pr = payload.get("pull_request", {})
        requested_reviewer = payload.get("requested_reviewer", {})
        repository = payload.get("repository", {})

        return {
            "status": "accepted",
            "action": action,
            "pr_number": pr.get("number"),
            "pr_title": pr.get("title"),
            "requested_reviewer": requested_reviewer.get("login"),
            "repository": repository.get("full_name"),
        }

    # Other pull_request actions are acknowledged but not processed
    return {"status": "ignored", "action": action}
