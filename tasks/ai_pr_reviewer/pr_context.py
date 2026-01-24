"""PR context gathering for AI PR Reviewer.

Fetches comprehensive PR context from GitHub API including metadata,
changed files with diffs, and commit history.
"""

from dataclasses import dataclass

import httpx


@dataclass
class PRMetadata:
    """Metadata about a pull request."""

    number: int
    title: str
    body: str | None
    author: str
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    html_url: str
    state: str
    draft: bool


@dataclass
class PRCommit:
    """A commit in a pull request."""

    sha: str
    message: str
    author_name: str
    author_email: str


@dataclass
class FileChange:
    """A changed file in a pull request."""

    filename: str
    status: str  # added, removed, modified, renamed, copied
    additions: int
    deletions: int
    changes: int
    patch: str | None  # The diff patch, None for binary files
    previous_filename: str | None  # For renamed files


@dataclass
class PRContext:
    """Complete context for a pull request review."""

    metadata: PRMetadata
    commits: list[PRCommit]
    files: list[FileChange]

    @property
    def total_additions(self) -> int:
        """Total lines added across all files."""
        return sum(f.additions for f in self.files)

    @property
    def total_deletions(self) -> int:
        """Total lines deleted across all files."""
        return sum(f.deletions for f in self.files)


class PRContextError(Exception):
    """Raised when PR context cannot be fetched."""

    pass


async def fetch_pr_metadata(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> PRMetadata:
    """
    Fetch PR metadata from GitHub API.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        PRMetadata with all PR information.

    Raises:
        PRContextError: If the PR cannot be fetched.
        httpx.HTTPStatusError: If the GitHub API request fails.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        if response.status_code == 404:
            raise PRContextError(
                f"Pull request #{pr_number} not found in {owner}/{repo}"
            )

        response.raise_for_status()

        data = response.json()

        return PRMetadata(
            number=data["number"],
            title=data["title"],
            body=data.get("body"),
            author=data["user"]["login"],
            base_branch=data["base"]["ref"],
            head_branch=data["head"]["ref"],
            base_sha=data["base"]["sha"],
            head_sha=data["head"]["sha"],
            html_url=data["html_url"],
            state=data["state"],
            draft=data.get("draft", False),
        )

    finally:
        if should_close_client:
            await client.aclose()


async def fetch_pr_commits(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[PRCommit]:
    """
    Fetch all commits in a pull request.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        List of PRCommit objects in chronological order.

    Raises:
        PRContextError: If commits cannot be fetched.
        httpx.HTTPStatusError: If the GitHub API request fails.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        commits: list[PRCommit] = []
        page = 1
        per_page = 100

        while True:
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/commits"
            )
            response = await client.get(
                url,
                params={"page": page, "per_page": per_page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 404:
                raise PRContextError(
                    f"Pull request #{pr_number} not found in {owner}/{repo}"
                )

            response.raise_for_status()

            data = response.json()
            if not data:
                break

            for commit_data in data:
                commit_info = commit_data["commit"]
                commits.append(
                    PRCommit(
                        sha=commit_data["sha"],
                        message=commit_info["message"],
                        author_name=commit_info["author"]["name"],
                        author_email=commit_info["author"]["email"],
                    )
                )

            if len(data) < per_page:
                break

            page += 1

        return commits

    finally:
        if should_close_client:
            await client.aclose()


async def fetch_pr_files(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[FileChange]:
    """
    Fetch all changed files in a pull request with diffs.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        List of FileChange objects with diff patches.

    Raises:
        PRContextError: If files cannot be fetched.
        httpx.HTTPStatusError: If the GitHub API request fails.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        files: list[FileChange] = []
        page = 1
        per_page = 100

        while True:
            url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
            response = await client.get(
                url,
                params={"page": page, "per_page": per_page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 404:
                raise PRContextError(
                    f"Pull request #{pr_number} not found in {owner}/{repo}"
                )

            response.raise_for_status()

            data = response.json()
            if not data:
                break

            for file_data in data:
                files.append(
                    FileChange(
                        filename=file_data["filename"],
                        status=file_data["status"],
                        additions=file_data["additions"],
                        deletions=file_data["deletions"],
                        changes=file_data["changes"],
                        patch=file_data.get("patch"),
                        previous_filename=file_data.get("previous_filename"),
                    )
                )

            if len(data) < per_page:
                break

            page += 1

        return files

    finally:
        if should_close_client:
            await client.aclose()


async def fetch_pr_context(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> PRContext:
    """
    Fetch complete PR context including metadata, commits, and files.

    This is a convenience function that fetches all PR information
    in parallel for efficiency.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        PRContext with complete PR information.

    Raises:
        PRContextError: If any part of the context cannot be fetched.
        httpx.HTTPStatusError: If the GitHub API request fails.
    """
    import asyncio

    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # Fetch all data in parallel for efficiency
        metadata_task = fetch_pr_metadata(owner, repo, pr_number, token, client)
        commits_task = fetch_pr_commits(owner, repo, pr_number, token, client)
        files_task = fetch_pr_files(owner, repo, pr_number, token, client)

        metadata, commits, files = await asyncio.gather(
            metadata_task, commits_task, files_task
        )

        return PRContext(
            metadata=metadata,
            commits=commits,
            files=files,
        )

    finally:
        if should_close_client:
            await client.aclose()
