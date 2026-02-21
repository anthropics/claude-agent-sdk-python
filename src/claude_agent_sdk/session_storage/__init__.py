"""Session storage backends for cloud persistence.

This module provides abstractions and implementations for storing session
transcripts in cloud storage, enabling:

- Horizontal scaling across multiple servers with shared sessions
- Support for ephemeral filesystems (containers, serverless)
- Extensible architecture for custom backends

WARNING: Direct cloud storage operations add latency (50-500ms+ per operation).
For production at scale, consider wrapping implementations with a caching layer.

Example:
    Basic usage with S3:

    >>> from claude_agent_sdk import ClaudeAgentOptions
    >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
    >>>
    >>> storage = S3SessionStorage(S3Config(
    ...     bucket="my-sessions",
    ...     prefix="claude",
    ...     region="us-east-1",
    ... ))
    >>>
    >>> options = ClaudeAgentOptions(session_storage=storage)

    Custom caching wrapper (for production scale):

    >>> class CachedStorage:
    ...     def __init__(self, backend, cache):
    ...         self.backend = backend
    ...         self.cache = cache
    ...
    ...     async def download_transcript(self, session_id, local_path):
    ...         if await self.cache.has(session_id):
    ...             return await self.cache.get(session_id, local_path)
    ...         return await self.backend.download_transcript(session_id, local_path)

Available backends:
    - S3SessionStorage: AWS S3 (requires: pip install claude-agent-sdk[s3])
    - GCSSessionStorage: Google Cloud Storage (requires: pip install claude-agent-sdk[gcs])

See Also:
    - SessionStorage: Protocol for implementing custom backends
    - BaseSessionStorage: Base class with retry logic
"""

from ._base import BaseSessionStorage
from ._protocol import SessionMetadata, SessionStorage

__all__ = [
    # Protocol and base class
    "SessionStorage",
    "SessionMetadata",
    "BaseSessionStorage",
]


# Lazy imports for optional cloud dependencies
def __getattr__(name: str) -> object:
    """Lazy import cloud storage implementations.

    This allows importing the main module without requiring boto3 or
    google-cloud-storage to be installed.
    """
    if name == "S3SessionStorage":
        from ._s3 import S3SessionStorage

        return S3SessionStorage
    if name == "S3Config":
        from ._s3 import S3Config

        return S3Config
    if name == "GCSSessionStorage":
        from ._gcs import GCSSessionStorage

        return GCSSessionStorage
    if name == "GCSConfig":
        from ._gcs import GCSConfig

        return GCSConfig
    if name == "SessionSyncManager":
        from ._sync import SessionSyncManager

        return SessionSyncManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
