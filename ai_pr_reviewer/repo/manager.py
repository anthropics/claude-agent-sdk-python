"""Repository cloning and management."""

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ClonedRepo:
    """A cloned repository."""

    path: Path
    owner: str
    repo: str
    head_sha: str

    def cleanup(self) -> None:
        """Remove the cloned repository."""
        if self.path.exists():
            shutil.rmtree(self.path)
            logger.info(f"Cleaned up cloned repo at {self.path}")


class RepoManager:
    """Manages repository cloning for reviews."""

    def __init__(self, base_dir: Path | None = None) -> None:
        """Initialize repo manager with optional base directory."""
        self.base_dir = base_dir or Path(tempfile.gettempdir()) / "ai-pr-reviewer"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def clone(
        self,
        owner: str,
        repo: str,
        head_ref: str,
        head_sha: str,
        token: str,
    ) -> ClonedRepo:
        """Clone a repository for review."""
        # Create unique directory for this clone
        clone_dir = self.base_dir / owner / repo / head_sha[:8]
        clone_dir.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing clone if present
        if clone_dir.exists():
            shutil.rmtree(clone_dir)

        # Clone URL with token for authentication
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        logger.info(f"Cloning {owner}/{repo}@{head_ref} to {clone_dir}")

        # Shallow clone the specific branch
        process = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            head_ref,
            clone_url,
            str(clone_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"Failed to clone repository: {error_msg}")
            raise RuntimeError(f"Git clone failed: {error_msg}")

        logger.info(f"Successfully cloned {owner}/{repo} to {clone_dir}")

        return ClonedRepo(
            path=clone_dir,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
        )

    async def cleanup(self, cloned_repo: ClonedRepo) -> None:
        """Clean up a cloned repository."""
        try:
            cloned_repo.cleanup()
        except Exception as e:
            logger.warning(f"Failed to cleanup repo: {e}")
