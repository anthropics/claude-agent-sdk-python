"""Application configuration from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Application configuration."""

    github_app_id: str
    github_private_key: str
    github_webhook_secret: str
    anthropic_api_key: str
    port: int = 8000
    host: str = "0.0.0.0"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        # GitHub App ID
        app_id = os.environ.get("GITHUB_APP_ID", "")
        if not app_id:
            raise ValueError("GITHUB_APP_ID environment variable is required")

        # GitHub Private Key - can be path or direct content
        private_key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")

        if private_key_path:
            private_key = Path(private_key_path).read_text()
        elif not private_key:
            raise ValueError(
                "GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH is required"
            )

        # Anthropic API Key
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")

        return cls(
            github_app_id=app_id,
            github_private_key=private_key,
            github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
            anthropic_api_key=anthropic_api_key,
            port=int(os.environ.get("PORT", "8000")),
            host=os.environ.get("HOST", "0.0.0.0"),
        )
