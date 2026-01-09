"""Session storage protocol definition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class SessionMetadata:
    """Metadata about a stored session.

    Attributes:
        session_id: Unique identifier for the session.
        created_at: Unix timestamp when session was first stored.
        updated_at: Unix timestamp when session was last updated.
        size_bytes: Size of the transcript in bytes.
        storage_key: Backend-specific storage key/path.
    """

    session_id: str
    created_at: float
    updated_at: float
    size_bytes: int
    storage_key: str


@runtime_checkable
class SessionStorage(Protocol):
    """Protocol for session storage backends.

    Implementations provide async methods for uploading, downloading,
    and managing session transcripts in cloud storage.

    The Claude Code CLI writes transcripts to local paths. This protocol
    abstracts syncing those local files to/from cloud storage for persistence
    across ephemeral environments and horizontal scaling.

    WARNING: Direct cloud storage operations add latency (50-500ms+ per operation).
    For production at scale, consider wrapping implementations with a caching layer
    (Redis, local LRU cache, etc.).

    Example:
        Basic usage with S3:

        >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
        >>> storage = S3SessionStorage(S3Config(bucket="my-bucket"))
        >>> await storage.upload_transcript("session-123", "/tmp/transcript.jsonl")
        'claude-sessions/session-123/transcript.jsonl'

        Custom implementation:

        >>> class MyStorage:
        ...     async def upload_transcript(self, session_id, local_path):
        ...         # Upload to your backend
        ...         return f"my-backend/{session_id}"
        ...
        ...     async def download_transcript(self, session_id, local_path):
        ...         # Download from your backend
        ...         return True
        ...
        ...     # ... implement other methods
    """

    async def upload_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> str:
        """Upload a local transcript file to cloud storage.

        Args:
            session_id: The session identifier.
            local_path: Path to the local transcript file.

        Returns:
            The cloud storage key/URL for the uploaded file.

        Raises:
            SessionStorageError: If upload fails.
        """
        ...

    async def download_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> bool:
        """Download a transcript from cloud storage to local path.

        Args:
            session_id: The session identifier.
            local_path: Where to save the downloaded file.

        Returns:
            True if download succeeded, False if session not found.

        Raises:
            SessionStorageError: If download fails for reasons other than not found.
        """
        ...

    async def exists(self, session_id: str) -> bool:
        """Check if a session exists in cloud storage.

        Args:
            session_id: The session identifier.

        Returns:
            True if session exists in storage.
        """
        ...

    async def delete(self, session_id: str) -> bool:
        """Delete a session from cloud storage.

        Args:
            session_id: The session identifier.

        Returns:
            True if deleted, False if not found.
        """
        ...

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
        ...

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata for a session.

        Args:
            session_id: The session identifier.

        Returns:
            Session metadata or None if not found.
        """
        ...
