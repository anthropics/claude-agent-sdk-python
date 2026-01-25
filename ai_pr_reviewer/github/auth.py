"""GitHub App authentication."""

import time
from dataclasses import dataclass

import httpx
import jwt


@dataclass
class InstallationToken:
    """GitHub App installation token."""

    token: str
    expires_at: str


class GitHubAuth:
    """GitHub App authentication handler."""

    def __init__(self, app_id: str, private_key: str) -> None:
        """Initialize with GitHub App credentials."""
        self.app_id = app_id
        self.private_key = private_key
        self._token_cache: dict[int, InstallationToken] = {}

    def generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago to handle clock skew
            "exp": now + 600,  # Expires in 10 minutes
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        """Get an installation access token for the given installation."""
        # Check cache
        cached = self._token_cache.get(installation_id)
        if cached:
            # TODO: Check expiration and refresh if needed
            return cached.token

        # Request new token
        app_jwt = self.generate_jwt()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()

            data = response.json()
            token = InstallationToken(
                token=data["token"],
                expires_at=data["expires_at"],
            )

            # Cache the token
            self._token_cache[installation_id] = token
            return token.token
