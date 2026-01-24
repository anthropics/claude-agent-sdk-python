"""Configuration for the AI PR Reviewer app."""

import os
from dataclasses import dataclass
from pathlib import Path


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

        # Support loading private key from file path or direct content
        private_key = os.environ.get("GITHUB_PRIVATE_KEY", "")
        private_key_path = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "")

        if not private_key and private_key_path:
            key_file = Path(private_key_path)
            # Handle relative paths from the config file location
            if not key_file.is_absolute():
                key_file = Path(__file__).parent / key_file
            if key_file.exists():
                private_key = key_file.read_text()

        return cls(
            github_webhook_secret=webhook_secret,
            github_app_id=app_id,
            github_private_key=private_key,
        )
