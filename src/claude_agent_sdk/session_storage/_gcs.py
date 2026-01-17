"""Google Cloud Storage session storage implementation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from ._base import BaseSessionStorage
from ._protocol import SessionMetadata

if TYPE_CHECKING:
    from google.cloud.storage import Bucket, Client  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


@dataclass
class GCSConfig:
    """Configuration for Google Cloud Storage session storage.

    Attributes:
        bucket: GCS bucket name (required).
        prefix: Key prefix for organizing sessions. Defaults to "claude-sessions".
        project: GCP project ID. If None, uses default from credentials.
        credentials_path: Path to service account JSON key file. If None, uses
            Application Default Credentials (ADC).

    Example:
        Using service account credentials:

        >>> config = GCSConfig(
        ...     bucket="my-sessions",
        ...     prefix="claude-prod",
        ...     project="my-gcp-project",
        ...     credentials_path="/path/to/service-account.json"
        ... )

        Using Application Default Credentials:

        >>> config = GCSConfig(bucket="my-sessions")
    """

    bucket: str
    prefix: str = "claude-sessions"
    project: str | None = None
    credentials_path: str | None = None


class GCSSessionStorage(BaseSessionStorage):
    """Google Cloud Storage session storage implementation.

    WARNING: GCS operations add latency (50-500ms+ per operation). For production
    at scale, consider wrapping with a caching layer (Redis, local LRU, etc.).

    This implementation uses the google-cloud-storage library and wraps synchronous
    operations with anyio.to_thread.run_sync() for async compatibility.

    Authentication:
        The client authenticates using:
        1. Service account JSON file if credentials_path is provided
        2. Application Default Credentials (ADC) otherwise:
           - GOOGLE_APPLICATION_CREDENTIALS environment variable
           - gcloud CLI credentials
           - Compute Engine/GKE/Cloud Run service account

    Installation:
        This implementation requires google-cloud-storage:

        >>> pip install claude-agent-sdk[gcs]

        Or install directly:

        >>> pip install google-cloud-storage

    Example:
        Basic usage:

        >>> from claude_agent_sdk.session_storage import GCSSessionStorage, GCSConfig
        >>> storage = GCSSessionStorage(GCSConfig(
        ...     bucket="my-sessions",
        ...     prefix="claude",
        ...     project="my-gcp-project"
        ... ))
        >>> # Upload a transcript
        >>> key = await storage.upload_transcript(
        ...     "session-123",
        ...     "/tmp/transcript.jsonl"
        ... )
        >>> print(f"Uploaded to: {key}")
        'claude/session-123/transcript.jsonl'
        >>>
        >>> # Download a transcript
        >>> success = await storage.download_transcript(
        ...     "session-123",
        ...     "/tmp/restored.jsonl"
        ... )
        >>>
        >>> # List sessions
        >>> sessions = await storage.list_sessions(prefix="prod-", limit=50)
        >>> for meta in sessions:
        ...     print(f"{meta.session_id}: {meta.size_bytes} bytes")

        With service account credentials:

        >>> storage = GCSSessionStorage(GCSConfig(
        ...     bucket="my-sessions",
        ...     credentials_path="/path/to/service-account.json"
        ... ))

    Attributes:
        config: GCS configuration.
        max_retries: Maximum retry attempts (inherited from BaseSessionStorage).
        retry_delay: Base delay between retries (inherited from BaseSessionStorage).
    """

    def __init__(
        self,
        config: GCSConfig,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize GCS session storage.

        Args:
            config: GCS configuration.
            max_retries: Maximum retry attempts for failed operations.
            retry_delay: Base delay in seconds between retries.

        Raises:
            ImportError: If google-cloud-storage is not installed.
        """
        super().__init__(
            prefix=config.prefix,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self.config = config
        self._client: Client | None = None
        self._bucket: Bucket | None = None

        # Validate import on init
        self._ensure_gcs_available()

    def _ensure_gcs_available(self) -> None:
        """Ensure google-cloud-storage is installed.

        Raises:
            ImportError: If google-cloud-storage is not installed.
        """
        try:
            import google.cloud.storage  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "google-cloud-storage is required for GCSSessionStorage. "
                "Install it with: pip install claude-agent-sdk[gcs] "
                "or: pip install google-cloud-storage"
            ) from e

    def _get_client(self) -> Client:
        """Get or create the GCS client (lazy initialization).

        Returns:
            Initialized GCS client.
        """
        if self._client is None:
            # ruff: noqa: I001
            from google.cloud import storage  # type: ignore[import-not-found,import-untyped,unused-ignore]
            from google.oauth2 import service_account  # type: ignore[import-not-found,import-untyped,unused-ignore]

            if self.config.credentials_path:
                # Use service account credentials from file
                credentials = service_account.Credentials.from_service_account_file(
                    self.config.credentials_path
                )
                self._client = storage.Client(
                    project=self.config.project,
                    credentials=credentials,
                )
            else:
                # Use Application Default Credentials
                self._client = storage.Client(project=self.config.project)

        return self._client

    def _get_bucket(self) -> Bucket:
        """Get or create the GCS bucket reference (lazy initialization).

        Returns:
            GCS bucket object.
        """
        if self._bucket is None:
            client = self._get_client()
            self._bucket = client.bucket(self.config.bucket)
        return self._bucket

    async def _do_upload(self, key: str, local_path: Path) -> None:
        """Upload a file to GCS.

        Args:
            key: GCS object key.
            local_path: Local file to upload.

        Raises:
            Exception: On upload failure.
        """

        def _sync_upload() -> None:
            bucket = self._get_bucket()
            blob = bucket.blob(key)
            blob.upload_from_filename(str(local_path))

        await anyio.to_thread.run_sync(_sync_upload)

    async def _do_download(self, key: str, local_path: Path) -> bool:
        """Download a file from GCS.

        Args:
            key: GCS object key.
            local_path: Local path to save file.

        Returns:
            True if downloaded, False if not found.

        Raises:
            Exception: On download failure (other than not found).
        """

        def _sync_download() -> bool:
            from google.api_core.exceptions import NotFound  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: I001

            bucket = self._get_bucket()
            blob = bucket.blob(key)

            try:
                blob.download_to_filename(str(local_path))
                return True
            except NotFound:
                return False

        return await anyio.to_thread.run_sync(_sync_download)

    async def _do_exists(self, key: str) -> bool:
        """Check if an object exists in GCS.

        Args:
            key: GCS object key.

        Returns:
            True if exists.
        """

        def _sync_exists() -> bool:
            bucket = self._get_bucket()
            blob = bucket.blob(key)
            return bool(blob.exists())

        return await anyio.to_thread.run_sync(_sync_exists)

    async def _do_delete(self, key: str) -> bool:
        """Delete an object from GCS.

        Args:
            key: GCS object key.

        Returns:
            True if deleted, False if not found.
        """

        def _sync_delete() -> bool:
            from google.api_core.exceptions import NotFound  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: I001

            bucket = self._get_bucket()
            blob = bucket.blob(key)

            try:
                blob.delete()
                return True
            except NotFound:
                return False

        return await anyio.to_thread.run_sync(_sync_delete)

    async def _do_list(self, prefix: str, limit: int) -> list[SessionMetadata]:
        """List objects in GCS with given prefix.

        Args:
            prefix: GCS key prefix.
            limit: Maximum number of items to return.

        Returns:
            List of session metadata.
        """

        def _sync_list() -> list[SessionMetadata]:
            bucket = self._get_bucket()
            blobs = bucket.list_blobs(prefix=prefix, max_results=limit)

            results: list[SessionMetadata] = []
            for blob in blobs:
                # Extract session_id from key
                session_id = self._extract_session_id(blob.name)
                if not session_id:
                    continue

                # Convert timestamps
                created_at = blob.time_created.timestamp() if blob.time_created else 0.0
                updated_at = blob.updated.timestamp() if blob.updated else created_at

                metadata = SessionMetadata(
                    session_id=session_id,
                    created_at=created_at,
                    updated_at=updated_at,
                    size_bytes=blob.size or 0,
                    storage_key=blob.name,
                )
                results.append(metadata)

            return results

        return await anyio.to_thread.run_sync(_sync_list)

    async def _do_get_metadata(self, key: str) -> SessionMetadata | None:
        """Get metadata for a specific object in GCS.

        Args:
            key: GCS object key.

        Returns:
            Session metadata or None if not found.
        """

        def _sync_get_metadata() -> SessionMetadata | None:
            from google.api_core.exceptions import NotFound  # type: ignore[import-not-found,import-untyped,unused-ignore]  # noqa: I001

            bucket = self._get_bucket()
            blob = bucket.blob(key)

            try:
                # Reload to fetch metadata
                blob.reload()
            except NotFound:
                return None

            # Extract session_id from key
            session_id = self._extract_session_id(blob.name)
            if not session_id:
                return None

            # Convert timestamps
            created_at = blob.time_created.timestamp() if blob.time_created else 0.0
            updated_at = blob.updated.timestamp() if blob.updated else created_at

            return SessionMetadata(
                session_id=session_id,
                created_at=created_at,
                updated_at=updated_at,
                size_bytes=blob.size or 0,
                storage_key=blob.name,
            )

        return await anyio.to_thread.run_sync(_sync_get_metadata)
