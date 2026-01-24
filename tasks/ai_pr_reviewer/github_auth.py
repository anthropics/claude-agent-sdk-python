"""GitHub App authentication module."""

import time
from dataclasses import dataclass, field

import httpx
import jwt


@dataclass
class CachedToken:
    """A cached access token with expiration tracking."""

    token: str
    expires_at: float  # Unix timestamp


@dataclass
class GitHubAppAuth:
    """
    GitHub App authentication handler.

    Generates JWTs for app authentication and manages installation access tokens
    with caching and automatic expiration handling.
    """

    app_id: str
    private_key: str
    _token_cache: dict[int, CachedToken] = field(default_factory=dict)

    # JWT is valid for 10 minutes, but we refresh 60 seconds early
    JWT_EXPIRATION_SECONDS: int = 600
    JWT_REFRESH_BUFFER_SECONDS: int = 60

    # Installation tokens valid for 1 hour, refresh 5 minutes early
    TOKEN_REFRESH_BUFFER_SECONDS: int = 300

    def generate_jwt(self) -> str:
        """
        Generate a JWT for GitHub App authentication.

        The JWT is signed with the app's private key and is valid for 10 minutes.
        GitHub requires JWTs for app-level API requests.

        Returns:
            A signed JWT string.
        """
        now = int(time.time())

        payload = {
            # Issued at time (60 seconds in the past to allow for clock drift)
            "iat": now - 60,
            # JWT expiration time (10 minutes max)
            "exp": now + self.JWT_EXPIRATION_SECONDS,
            # GitHub App's ID
            "iss": self.app_id,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_installation_token(
        self,
        installation_id: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> str:
        """
        Get an installation access token for a specific installation.

        Tokens are cached and reused until they are close to expiration.
        This method handles the token exchange with GitHub's API.

        Args:
            installation_id: The GitHub App installation ID.
            http_client: Optional httpx client for making requests.

        Returns:
            An installation access token string.

        Raises:
            httpx.HTTPStatusError: If the GitHub API request fails.
        """
        # Check cache first
        cached = self._token_cache.get(installation_id)
        if cached and not self._is_token_expired(cached):
            return cached.token

        # Generate new token
        app_jwt = self.generate_jwt()

        should_close_client = http_client is None
        client = http_client or httpx.AsyncClient()

        try:
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
            token: str = data["token"]
            expires_at_str: str = data["expires_at"]

            # Parse ISO 8601 timestamp to Unix timestamp
            # Format: "2024-01-01T00:00:00Z"
            from datetime import datetime

            expires_at_dt = datetime.fromisoformat(
                expires_at_str.replace("Z", "+00:00")
            )
            expires_at = expires_at_dt.timestamp()

            # Cache the token
            self._token_cache[installation_id] = CachedToken(
                token=token,
                expires_at=expires_at,
            )

            return token
        finally:
            if should_close_client:
                await client.aclose()

    def _is_token_expired(self, cached: CachedToken) -> bool:
        """
        Check if a cached token is expired or close to expiration.

        Args:
            cached: The cached token to check.

        Returns:
            True if the token should be refreshed, False otherwise.
        """
        return time.time() >= (cached.expires_at - self.TOKEN_REFRESH_BUFFER_SECONDS)

    def clear_cache(self) -> None:
        """Clear all cached tokens."""
        self._token_cache.clear()

    def invalidate_token(self, installation_id: int) -> None:
        """
        Invalidate a specific cached token.

        Args:
            installation_id: The installation ID whose token should be invalidated.
        """
        self._token_cache.pop(installation_id, None)
