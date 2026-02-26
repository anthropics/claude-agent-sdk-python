"""S3 session storage implementation.

This module provides an S3-backed session storage implementation using
aiobotocore for async operations.

WARNING: S3 operations add latency (50-500ms per operation). For production
at scale, consider wrapping with a caching layer (Redis, local LRU, etc.).

Installation:
    pip install claude-agent-sdk[s3]

    Or install aiobotocore directly:
    pip install aiobotocore

Example:
    Basic AWS S3 usage:

    >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
    >>>
    >>> storage = S3SessionStorage(S3Config(
    ...     bucket="my-sessions",
    ...     prefix="claude-sessions",
    ...     region="us-east-1",
    ... ))
    >>> await storage.upload_transcript("session-123", "/tmp/transcript.jsonl")
    'claude-sessions/session-123/transcript.jsonl'

    With explicit credentials:

    >>> storage = S3SessionStorage(S3Config(
    ...     bucket="my-sessions",
    ...     aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
    ...     aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ...     region="us-east-1",
    ... ))

    S3-compatible services (MinIO, Cloudflare R2, DigitalOcean Spaces):

    >>> # MinIO
    >>> storage = S3SessionStorage(S3Config(
    ...     bucket="my-sessions",
    ...     endpoint_url="https://minio.example.com",
    ...     aws_access_key_id="minioadmin",
    ...     aws_secret_access_key="minioadmin",
    ... ))
    >>>
    >>> # Cloudflare R2
    >>> storage = S3SessionStorage(S3Config(
    ...     bucket="my-sessions",
    ...     endpoint_url="https://account-id.r2.cloudflarestorage.com",
    ...     aws_access_key_id="your-r2-access-key",
    ...     aws_secret_access_key="your-r2-secret-key",
    ... ))

Notes:
    - If credentials not provided, uses AWS credential chain:
      environment vars, IAM roles, shared credentials file
    - The client is lazy-initialized on first use
    - Call close() to clean up resources when done
    - S3-compatible services work with endpoint_url parameter
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import BaseSessionStorage
from ._protocol import SessionMetadata

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Check for aiobotocore availability
try:
    import aiobotocore.session  # type: ignore[import-not-found]
    from botocore.exceptions import ClientError  # type: ignore[import-not-found]

    _HAS_AIOBOTOCORE = True
except ImportError:
    _HAS_AIOBOTOCORE = False


@dataclass
class S3Config:
    """Configuration for S3 session storage.

    Attributes:
        bucket: S3 bucket name (required).
        prefix: Key prefix for organizing sessions (default: "claude-sessions").
        region: AWS region (optional, uses default if not set).
        endpoint_url: Custom S3 endpoint for S3-compatible services (optional).
                     Examples: MinIO, Cloudflare R2, DigitalOcean Spaces.
        aws_access_key_id: AWS access key (optional, uses credential chain if not set).
        aws_secret_access_key: AWS secret key (optional, uses credential chain if not set).

    Example:
        AWS S3:
        >>> config = S3Config(bucket="my-bucket", region="us-east-1")

        MinIO:
        >>> config = S3Config(
        ...     bucket="my-bucket",
        ...     endpoint_url="https://minio.example.com",
        ...     aws_access_key_id="admin",
        ...     aws_secret_access_key="password",
        ... )
    """

    bucket: str
    prefix: str = "claude-sessions"
    region: str | None = None
    endpoint_url: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None


class S3SessionStorage(BaseSessionStorage):
    """S3-backed session storage with async operations.

    Uses aiobotocore for efficient async S3 operations. Supports both AWS S3
    and S3-compatible services (MinIO, Cloudflare R2, etc.).

    WARNING: S3 operations add latency (50-500ms per operation). For production
    at scale, consider wrapping with a caching layer.

    The S3 client is lazy-initialized on first use to avoid connection overhead
    during initialization.

    Attributes:
        config: S3 configuration.
        max_retries: Maximum retry attempts (inherited from BaseSessionStorage).
        retry_delay: Base retry delay in seconds (inherited from BaseSessionStorage).

    Example:
        >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
        >>>
        >>> config = S3Config(bucket="my-sessions", region="us-east-1")
        >>> storage = S3SessionStorage(config)
        >>>
        >>> # Upload a session
        >>> key = await storage.upload_transcript("session-123", "/tmp/transcript.jsonl")
        >>>
        >>> # Download a session
        >>> success = await storage.download_transcript("session-123", "/tmp/downloaded.jsonl")
        >>>
        >>> # List sessions
        >>> sessions = await storage.list_sessions(limit=10)
        >>>
        >>> # Clean up
        >>> await storage.close()

    Note:
        Call close() when done to properly clean up the S3 client connection pool.
    """

    def __init__(
        self,
        config: S3Config,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize S3 session storage.

        Args:
            config: S3 configuration.
            max_retries: Maximum retry attempts for failed operations.
            retry_delay: Base delay in seconds between retries.

        Raises:
            ImportError: If aiobotocore is not installed.
        """
        if not _HAS_AIOBOTOCORE:
            raise ImportError(
                "aiobotocore is required for S3 session storage. "
                "Install with: pip install claude-agent-sdk[s3]"
            )

        super().__init__(
            prefix=config.prefix, max_retries=max_retries, retry_delay=retry_delay
        )
        self.config = config
        self._client: S3Client | None = None
        self._session: aiobotocore.session.AioSession | None = None
        self._client_context: object | None = None

    async def _get_client(self) -> S3Client:
        """Get or create the S3 client.

        Lazy-initializes the client on first use.

        Returns:
            S3 client instance.
        """
        if self._client is not None:
            return self._client

        # Create session
        self._session = aiobotocore.session.get_session()

        # Build client config
        client_kwargs = {"service_name": "s3"}

        if self.config.region:
            client_kwargs["region_name"] = self.config.region
        if self.config.endpoint_url:
            client_kwargs["endpoint_url"] = self.config.endpoint_url
        if self.config.aws_access_key_id:
            client_kwargs["aws_access_key_id"] = self.config.aws_access_key_id
        if self.config.aws_secret_access_key:
            client_kwargs["aws_secret_access_key"] = self.config.aws_secret_access_key

        # Create client context manager
        self._client_context = self._session.create_client(**client_kwargs)
        self._client = await self._client_context.__aenter__()  # type: ignore

        logger.debug(f"Initialized S3 client for bucket: {self.config.bucket}")
        return self._client

    async def close(self) -> None:
        """Close the S3 client and clean up resources.

        Should be called when done using the storage to properly close
        the connection pool.

        Example:
            >>> storage = S3SessionStorage(config)
            >>> try:
            ...     await storage.upload_transcript("session-123", "/tmp/transcript.jsonl")
            ... finally:
            ...     await storage.close()
        """
        if self._client_context is not None and self._client is not None:
            await self._client_context.__aexit__(None, None, None)  # type: ignore
            self._client = None
            self._client_context = None
            logger.debug("Closed S3 client")

    async def _do_upload(self, key: str, local_path: Path) -> None:
        """Upload a file to S3.

        Args:
            key: S3 key to upload to.
            local_path: Local file to upload.

        Raises:
            Exception: On upload failure.
        """
        client = await self._get_client()

        with local_path.open("rb") as f:
            await client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=f,
            )

    async def _do_download(self, key: str, local_path: Path) -> bool:
        """Download a file from S3.

        Args:
            key: S3 key to download from.
            local_path: Local path to save file.

        Returns:
            True if downloaded, False if not found.

        Raises:
            Exception: On download failure (other than not found).
        """
        client = await self._get_client()

        try:
            response = await client.get_object(
                Bucket=self.config.bucket,
                Key=key,
            )

            async with response["Body"] as stream:
                data = await stream.read()

            local_path.write_bytes(data)
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return False
            raise

    async def _do_exists(self, key: str) -> bool:
        """Check if a key exists in S3.

        Args:
            key: S3 key to check.

        Returns:
            True if exists.
        """
        client = await self._get_client()

        try:
            await client.head_object(
                Bucket=self.config.bucket,
                Key=key,
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    async def _do_delete(self, key: str) -> bool:
        """Delete a key from S3.

        Args:
            key: S3 key to delete.

        Returns:
            True if deleted, False if not found.
        """
        client = await self._get_client()

        # Check if exists first
        exists = await self._do_exists(key)
        if not exists:
            return False

        await client.delete_object(
            Bucket=self.config.bucket,
            Key=key,
        )
        return True

    async def _do_list(self, prefix: str, limit: int) -> list[SessionMetadata]:
        """List objects with given prefix.

        Args:
            prefix: S3 key prefix to list under.
            limit: Maximum items to return.

        Returns:
            List of session metadata.
        """
        client = await self._get_client()

        # Ensure prefix ends with / for directory-like listing
        if not prefix.endswith("/"):
            prefix = prefix + "/"

        response = await client.list_objects_v2(
            Bucket=self.config.bucket,
            Prefix=prefix,
            MaxKeys=limit,
        )

        results: list[SessionMetadata] = []
        contents = response.get("Contents", [])

        for obj in contents:
            key = obj["Key"]
            session_id = self._extract_session_id(key)

            if session_id:
                results.append(
                    SessionMetadata(
                        session_id=session_id,
                        created_at=obj["LastModified"].timestamp(),
                        updated_at=obj["LastModified"].timestamp(),
                        size_bytes=obj["Size"],
                        storage_key=key,
                    )
                )

        return results

    async def _do_get_metadata(self, key: str) -> SessionMetadata | None:
        """Get metadata for a specific key.

        Args:
            key: S3 key to get metadata for.

        Returns:
            Metadata or None if not found.
        """
        client = await self._get_client()

        try:
            response = await client.head_object(
                Bucket=self.config.bucket,
                Key=key,
            )

            session_id = self._extract_session_id(key)
            if not session_id:
                return None

            last_modified = response["LastModified"].timestamp()

            return SessionMetadata(
                session_id=session_id,
                created_at=last_modified,
                updated_at=last_modified,
                size_bytes=response["ContentLength"],
                storage_key=key,
            )

        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None
            raise
