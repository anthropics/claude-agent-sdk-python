"""Reviewer configuration parser for AI PR Reviewer.

Parses .ai-reviewer.yml files from repository roots to configure
AI reviewers with custom prompts and settings.
"""

from dataclasses import dataclass

import httpx
import yaml


@dataclass
class ReviewerSettings:
    """Configuration for a single AI reviewer persona."""

    username: str
    prompt: str
    language: str | None = None


@dataclass
class RepoReviewerConfig:
    """Repository-level reviewer configuration."""

    reviewers: dict[str, ReviewerSettings]

    def get_reviewer(self, username: str) -> ReviewerSettings | None:
        """
        Get reviewer settings by GitHub username.

        Args:
            username: The GitHub username to look up.

        Returns:
            ReviewerSettings if the username is configured, None otherwise.
        """
        return self.reviewers.get(username)

    def is_ai_reviewer(self, username: str) -> bool:
        """
        Check if a username is configured as an AI reviewer.

        Args:
            username: The GitHub username to check.

        Returns:
            True if the username is configured as an AI reviewer.
        """
        return username in self.reviewers


class ConfigNotFoundError(Exception):
    """Raised when no .ai-reviewer.yml file is found in the repository."""

    pass


class ConfigParseError(Exception):
    """Raised when the configuration file cannot be parsed."""

    pass


def parse_reviewer_config(yaml_content: str) -> RepoReviewerConfig:
    """
    Parse YAML content into a RepoReviewerConfig.

    Expected YAML format:
        reviewers:
          alice-ai:
            prompt: "You are a security-focused code reviewer..."
            language: "en"
          bob-ai:
            prompt: "You are a performance-focused reviewer..."

    Args:
        yaml_content: The raw YAML string to parse.

    Returns:
        A RepoReviewerConfig with all configured reviewers.

    Raises:
        ConfigParseError: If the YAML is invalid or missing required fields.
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ConfigParseError(f"Invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise ConfigParseError("Configuration must be a YAML mapping")

    reviewers_data = data.get("reviewers")
    if reviewers_data is None:
        raise ConfigParseError("Configuration must contain a 'reviewers' section")

    if not isinstance(reviewers_data, dict):
        raise ConfigParseError("'reviewers' must be a mapping of usernames to settings")

    reviewers: dict[str, ReviewerSettings] = {}

    for username, settings in reviewers_data.items():
        if not isinstance(username, str):
            raise ConfigParseError(
                f"Reviewer username must be a string, got {type(username).__name__}"
            )

        if not isinstance(settings, dict):
            raise ConfigParseError(f"Settings for '{username}' must be a mapping")

        prompt = settings.get("prompt")
        if prompt is None:
            raise ConfigParseError(f"Reviewer '{username}' must have a 'prompt' field")

        if not isinstance(prompt, str):
            raise ConfigParseError(f"Prompt for '{username}' must be a string")

        language = settings.get("language")
        if language is not None and not isinstance(language, str):
            raise ConfigParseError(f"Language for '{username}' must be a string")

        reviewers[username] = ReviewerSettings(
            username=username,
            prompt=prompt,
            language=language,
        )

    return RepoReviewerConfig(reviewers=reviewers)


async def fetch_reviewer_config(
    owner: str,
    repo: str,
    ref: str,
    token: str,
    http_client: httpx.AsyncClient | None = None,
) -> RepoReviewerConfig:
    """
    Fetch and parse the .ai-reviewer.yml file from a GitHub repository.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        ref: Git reference (branch, tag, or commit SHA) to fetch from.
        token: GitHub access token for authentication.
        http_client: Optional httpx client for making requests.

    Returns:
        A RepoReviewerConfig with all configured reviewers.

    Raises:
        ConfigNotFoundError: If no .ai-reviewer.yml file exists in the repo.
        ConfigParseError: If the file exists but cannot be parsed.
        httpx.HTTPStatusError: If the GitHub API request fails for other reasons.
    """
    should_close_client = http_client is None
    client = http_client or httpx.AsyncClient()

    try:
        # Fetch file contents from GitHub API
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/.ai-reviewer.yml"
        response = await client.get(
            url,
            params={"ref": ref},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        if response.status_code == 404:
            raise ConfigNotFoundError(
                f"No .ai-reviewer.yml found in {owner}/{repo} at ref {ref}"
            )

        response.raise_for_status()

        yaml_content = response.text
        return parse_reviewer_config(yaml_content)

    finally:
        if should_close_client:
            await client.aclose()
