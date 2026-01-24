"""Configuration for the AI PR Reviewer app."""

import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    """Application configuration loaded from environment variables."""

    github_webhook_secret: str
    github_app_id: str
    github_private_key: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables."""
        webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        app_id = os.environ.get("GITHUB_APP_ID", "")
        private_key = os.environ.get("GITHUB_PRIVATE_KEY", "")

        return cls(
            github_webhook_secret=webhook_secret,
            github_app_id=app_id,
            github_private_key=private_key,
        )
