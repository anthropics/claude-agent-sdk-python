"""Base class for session storage implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import anyio

from ._protocol import SessionMetadata

logger = logging.getLogger(__name__)


class BaseSessionStorage(ABC):
    """Abstract base class for session storage with common functionality.

    Provides shared logic like path normalization, key generation, and retry handling.
    Subclass this for concrete implementations (S3, GCS, etc.).

    WARNING: Direct cloud storage operations add latency (50-500ms+ per operation).
    For production at scale, consider wrapping with a caching layer.

    Attributes:
        prefix: Storage key prefix for organizing sessions.
        max_retries: Maximum retry attempts for failed operations.
        retry_delay: Base delay between retries (exponential backoff applied).

    Example:
        Implementing a custom backend:

        >>> class MyCloudStorage(BaseSessionStorage):
        ...     async def _do_upload(self, key: str, local_path: Path) -> None:
        ...         # Your upload logic here
        ...         pass
        ...
        ...     async def _do_download(self, key: str, local_path: Path) -> bool:
        ...         # Your download logic here
        ...         return True
        ...
        ...     # ... implement other abstract methods
    """

    def __init__(
        self,
        prefix: str = "claude-sessions",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize base session storage.

        Args:
            prefix: Storage key prefix for organizing sessions.
            max_retries: Maximum retry attempts for failed operations.
            retry_delay: Base delay in seconds between retries.
        """
        self.prefix = prefix.rstrip("/")
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _get_key(self, session_id: str) -> str:
        """Generate storage key for a session.

        Sanitizes session_id to prevent path traversal attacks.

        Args:
            session_id: The session identifier.

        Returns:
            Full storage key including prefix.
        """
        # Sanitize session_id to prevent path traversal
        safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return f"{self.prefix}/{safe_id}/transcript.jsonl"

    def _extract_session_id(self, key: str) -> str | None:
        """Extract session_id from a storage key.

        Args:
            key: Full storage key.

        Returns:
            Session ID or None if key doesn't match expected format.
        """
        if not key.startswith(self.prefix + "/"):
            return None
        remainder = key[len(self.prefix) + 1 :]
        parts = remainder.split("/")
        if len(parts) >= 1:
            return parts[0]
        return None

    @abstractmethod
    async def _do_upload(self, key: str, local_path: Path) -> None:
        """Backend-specific upload implementation.

        Args:
            key: Storage key to upload to.
            local_path: Local file to upload.

        Raises:
            Exception: On upload failure.
        """
        ...

    @abstractmethod
    async def _do_download(self, key: str, local_path: Path) -> bool:
        """Backend-specific download implementation.

        Args:
            key: Storage key to download from.
            local_path: Local path to save file.

        Returns:
            True if downloaded, False if not found.

        Raises:
            Exception: On download failure (other than not found).
        """
        ...

    @abstractmethod
    async def _do_exists(self, key: str) -> bool:
        """Backend-specific existence check.

        Args:
            key: Storage key to check.

        Returns:
            True if exists.
        """
        ...

    @abstractmethod
    async def _do_delete(self, key: str) -> bool:
        """Backend-specific delete implementation.

        Args:
            key: Storage key to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    async def _do_list(self, prefix: str, limit: int) -> list[SessionMetadata]:
        """Backend-specific list implementation.

        Args:
            prefix: Full prefix to list under.
            limit: Maximum items to return.

        Returns:
            List of session metadata.
        """
        ...

    @abstractmethod
    async def _do_get_metadata(self, key: str) -> SessionMetadata | None:
        """Backend-specific metadata retrieval.

        Args:
            key: Storage key to get metadata for.

        Returns:
            Metadata or None if not found.
        """
        ...

    async def upload_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> str:
        """Upload a local transcript file to cloud storage.

        Includes retry logic with exponential backoff.

        Args:
            session_id: The session identifier.
            local_path: Path to the local transcript file.

        Returns:
            The cloud storage key for the uploaded file.

        Raises:
            SessionStorageError: If upload fails after all retries.
        """
        from .._errors import SessionStorageError

        key = self._get_key(session_id)
        path = Path(local_path)

        if not path.exists():
            raise SessionStorageError(
                f"Local transcript not found: {path}",
                session_id=session_id,
                operation="upload",
            )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                await self._do_upload(key, path)
                logger.debug(f"Uploaded session {session_id} to {key}")
                return key
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Upload attempt {attempt + 1} failed for {session_id}, "
                        f"retrying in {delay}s: {e}"
                    )
                    await anyio.sleep(delay)

        raise SessionStorageError(
            f"Upload failed after {self.max_retries} attempts",
            session_id=session_id,
            operation="upload",
            original_error=last_error,
        )

    async def download_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> bool:
        """Download a transcript from cloud storage to local path.

        Includes retry logic with exponential backoff.

        Args:
            session_id: The session identifier.
            local_path: Where to save the downloaded file.

        Returns:
            True if download succeeded, False if session not found.

        Raises:
            SessionStorageError: If download fails after all retries.
        """
        from .._errors import SessionStorageError

        key = self._get_key(session_id)
        path = Path(local_path)

        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                result = await self._do_download(key, path)
                if result:
                    logger.debug(f"Downloaded session {session_id} to {path}")
                else:
                    logger.debug(f"Session {session_id} not found in storage")
                return result
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Download attempt {attempt + 1} failed for {session_id}, "
                        f"retrying in {delay}s: {e}"
                    )
                    await anyio.sleep(delay)

        raise SessionStorageError(
            f"Download failed after {self.max_retries} attempts",
            session_id=session_id,
            operation="download",
            original_error=last_error,
        )

    async def exists(self, session_id: str) -> bool:
        """Check if a session exists in cloud storage.

        Args:
            session_id: The session identifier.

        Returns:
            True if session exists in storage.
        """
        key = self._get_key(session_id)
        return await self._do_exists(key)

    async def delete(self, session_id: str) -> bool:
        """Delete a session from cloud storage.

        Args:
            session_id: The session identifier.

        Returns:
            True if deleted, False if not found.
        """
        key = self._get_key(session_id)
        result = await self._do_delete(key)
        if result:
            logger.debug(f"Deleted session {session_id}")
        return result

    async def list_sessions(
        self,
        prefix: str | None = None,
        limit: int = 100,
    ) -> list[SessionMetadata]:
        """List sessions in cloud storage.

        Args:
            prefix: Optional prefix filter for session IDs.
            limit: Maximum number of sessions to return.

        Returns:
            List of session metadata.
        """
        full_prefix = f"{self.prefix}/{prefix}" if prefix else self.prefix
        return await self._do_list(full_prefix, limit)

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata for a session.

        Args:
            session_id: The session identifier.

        Returns:
            Session metadata or None if not found.
        """
        key = self._get_key(session_id)
        return await self._do_get_metadata(key)
