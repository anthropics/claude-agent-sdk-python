"""GitHub REST API client."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GitHubAPI:
    """GitHub REST API client."""

    def __init__(self, token: str) -> None:
        """Initialize with access token."""
        self.token = token
        self.base_url = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str | None:
        """Get file content from repository."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/contents/{path}",
                headers=self._headers(),
                params={"ref": ref},
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            # Decode base64 content
            import base64

            return base64.b64decode(data["content"]).decode("utf-8")

    async def get_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        """Get pull request details."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_pull_request_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        """Get files changed in a pull request."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def create_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        path: str,
        line: int,
        body: str,
        side: str = "RIGHT",
    ) -> dict[str, Any]:
        """Create an inline review comment on a pull request."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers=self._headers(),
                json={
                    "commit_id": commit_id,
                    "path": path,
                    "line": line,
                    "side": side,
                    "body": body,
                },
            )
            response.raise_for_status()
            return response.json()

    async def submit_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        event: str,
    ) -> dict[str, Any]:
        """Submit a pull request review."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers=self._headers(),
                json={
                    "commit_id": commit_id,
                    "body": body,
                    "event": event,  # APPROVE, REQUEST_CHANGES, COMMENT
                },
            )
            response.raise_for_status()
            return response.json()

    async def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        """Create a comment on an issue or pull request."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}/comments",
                headers=self._headers(),
                json={"body": body},
            )
            response.raise_for_status()
            return response.json()
