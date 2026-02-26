#!/usr/bin/env python3
"""Caching patterns for session storage in production.

This file demonstrates how to build caching wrappers around session storage
backends to reduce latency and cost in production environments.

The SDK provides primitive session storage implementations (S3, GCS) that
directly interact with cloud storage. This "batteries included but removable"
philosophy lets you add caching optimized for your specific needs.

Caching strategies shown:
- Local file cache (simple, works anywhere)
- Redis cache (distributed, recommended for production)
- LRU memory cache (fast, but process-local)

Installation:
    # Base session storage
    pip install claude-agent-sdk[s3]

    # For Redis examples
    pip install redis

Why cache?
- Direct S3/GCS operations: 50-500ms+ latency per operation
- With local cache: <1ms for cache hits
- Cost savings: Fewer cloud storage API calls

WARNING: Caching adds complexity. Only add it when you have:
1. High request volume (>100 requests/min)
2. Measured latency problems
3. Monitoring to track cache hit rates
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.session_storage import SessionMetadata, SessionStorage
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

logger = logging.getLogger(__name__)


def display_message(msg):
    """Display messages in a standardized format."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"Claude: {block.text}")
    elif isinstance(msg, ResultMessage):
        print("Result ended")


# ============================================================================
# PATTERN 1: Local File Cache
# ============================================================================


class LocalFileCachedStorage:
    """File-based cache wrapper for session storage.

    This is the simplest caching strategy - keeps a local file cache of
    recently accessed transcripts. Perfect for single-server deployments
    or development environments.

    Benefits:
    - Simple to implement (no dependencies)
    - Works on any filesystem
    - Survives process restarts

    Limitations:
    - Not shared across servers
    - No automatic eviction (manual cleanup needed)
    - File I/O overhead (still faster than S3)

    Example:
        >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
        >>> backend = S3SessionStorage(S3Config(bucket="my-bucket"))
        >>> cached = LocalFileCachedStorage(backend, cache_dir="/tmp/session-cache")
        >>> options = ClaudeAgentOptions(session_storage=cached)
    """

    def __init__(
        self, backend: SessionStorage, cache_dir: str | Path = "/tmp/session-cache"
    ):
        """Initialize file cache wrapper.

        Args:
            backend: Underlying storage backend (S3, GCS, etc).
            cache_dir: Directory to store cached transcripts.
        """
        self.backend = backend
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized file cache at: {self.cache_dir}")

    def _cache_path(self, session_id: str) -> Path:
        """Get cache file path for a session."""
        return self.cache_dir / f"{session_id}.jsonl"

    async def upload_transcript(self, session_id: str, local_path: Path | str) -> str:
        """Upload transcript and update cache."""
        # Upload to backend
        result = await self.backend.upload_transcript(session_id, local_path)

        # Update local cache
        cache_path = self._cache_path(session_id)
        local_path = Path(local_path)
        if local_path.exists():
            # Copy to cache
            cache_path.write_bytes(local_path.read_bytes())
            logger.debug(f"Cached transcript for {session_id}")

        return result

    async def download_transcript(
        self, session_id: str, local_path: Path | str
    ) -> bool:
        """Download transcript, using cache if available."""
        cache_path = self._cache_path(session_id)
        local_path = Path(local_path)

        # Check cache first
        if cache_path.exists():
            logger.info(f"Cache HIT for {session_id}")
            local_path.write_bytes(cache_path.read_bytes())
            return True

        # Cache miss - fetch from backend
        logger.info(f"Cache MISS for {session_id}")
        success = await self.backend.download_transcript(session_id, local_path)

        if success:
            # Populate cache
            cache_path.write_bytes(local_path.read_bytes())
            logger.debug(f"Populated cache for {session_id}")

        return success

    async def exists(self, session_id: str) -> bool:
        """Check if session exists (cache or backend)."""
        # Check cache first
        if self._cache_path(session_id).exists():
            return True
        return await self.backend.exists(session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete from both cache and backend."""
        # Remove from cache
        cache_path = self._cache_path(session_id)
        if cache_path.exists():
            cache_path.unlink()

        # Remove from backend
        return await self.backend.delete(session_id)

    async def list_sessions(
        self, prefix: str | None = None, limit: int = 100
    ) -> list[SessionMetadata]:
        """List sessions from backend (cache doesn't affect listing)."""
        return await self.backend.list_sessions(prefix, limit)

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata from backend."""
        return await self.backend.get_metadata(session_id)

    def clear_cache(self) -> int:
        """Clear all cached files.

        Returns:
            Number of files removed.
        """
        count = 0
        for cache_file in self.cache_dir.glob("*.jsonl"):
            cache_file.unlink()
            count += 1
        logger.info(f"Cleared {count} cached files")
        return count


# ============================================================================
# PATTERN 2: Redis Cache (Production-Ready)
# ============================================================================


class RedisCachedStorage:
    """Redis-based cache wrapper for session storage.

    This is the recommended production caching strategy. Redis provides:
    - Distributed cache shared across all servers
    - Automatic TTL-based eviction
    - High performance (sub-millisecond access)
    - Built-in memory management

    Benefits:
    - Shared across all servers (consistent cache)
    - Automatic expiration (no manual cleanup)
    - Very fast (in-memory)
    - Production-proven

    Limitations:
    - Requires Redis server
    - Additional infrastructure cost
    - Memory constraints

    Example:
        >>> import redis.asyncio as redis
        >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
        >>>
        >>> backend = S3SessionStorage(S3Config(bucket="my-bucket"))
        >>> redis_client = await redis.from_url("redis://localhost")
        >>> cached = RedisCachedStorage(backend, redis_client)
        >>> options = ClaudeAgentOptions(session_storage=cached)
    """

    def __init__(
        self,
        backend: SessionStorage,
        redis_client: Any,  # redis.asyncio.Redis
        ttl: int = 3600,
        key_prefix: str = "claude:session:",
    ):
        """Initialize Redis cache wrapper.

        Args:
            backend: Underlying storage backend (S3, GCS, etc).
            redis_client: Redis client instance (redis.asyncio.Redis).
            ttl: Cache TTL in seconds (default: 1 hour).
            key_prefix: Prefix for Redis keys.
        """
        self.backend = backend
        self.redis = redis_client
        self.ttl = ttl
        self.key_prefix = key_prefix
        logger.info(f"Initialized Redis cache with TTL={ttl}s, prefix={key_prefix!r}")

    def _cache_key(self, session_id: str) -> str:
        """Get Redis key for a session."""
        return f"{self.key_prefix}{session_id}"

    async def upload_transcript(self, session_id: str, local_path: Path | str) -> str:
        """Upload transcript and update cache."""
        # Upload to backend
        result = await self.backend.upload_transcript(session_id, local_path)

        # Update Redis cache
        local_path = Path(local_path)
        if local_path.exists():
            cache_key = self._cache_key(session_id)
            content = local_path.read_bytes()
            await self.redis.setex(cache_key, self.ttl, content)
            logger.debug(f"Cached transcript for {session_id} in Redis")

        return result

    async def download_transcript(
        self, session_id: str, local_path: Path | str
    ) -> bool:
        """Download transcript, using Redis cache if available."""
        cache_key = self._cache_key(session_id)
        local_path = Path(local_path)

        # Check Redis first
        cached_content = await self.redis.get(cache_key)
        if cached_content:
            logger.info(f"Redis cache HIT for {session_id}")
            local_path.write_bytes(cached_content)
            return True

        # Cache miss - fetch from backend
        logger.info(f"Redis cache MISS for {session_id}")
        success = await self.backend.download_transcript(session_id, local_path)

        if success:
            # Populate Redis cache
            content = local_path.read_bytes()
            await self.redis.setex(cache_key, self.ttl, content)
            logger.debug(f"Populated Redis cache for {session_id}")

        return success

    async def exists(self, session_id: str) -> bool:
        """Check if session exists (cache or backend)."""
        # Check Redis first
        cache_key = self._cache_key(session_id)
        if await self.redis.exists(cache_key):
            return True
        return await self.backend.exists(session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete from both cache and backend."""
        # Remove from Redis
        cache_key = self._cache_key(session_id)
        await self.redis.delete(cache_key)

        # Remove from backend
        return await self.backend.delete(session_id)

    async def list_sessions(
        self, prefix: str | None = None, limit: int = 100
    ) -> list[SessionMetadata]:
        """List sessions from backend."""
        return await self.backend.list_sessions(prefix, limit)

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata from backend."""
        return await self.backend.get_metadata(session_id)

    async def clear_cache(self) -> int:
        """Clear all cached sessions from Redis.

        Returns:
            Number of keys removed.
        """
        pattern = f"{self.key_prefix}*"
        count = 0
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
            count += 1
        logger.info(f"Cleared {count} keys from Redis cache")
        return count


# ============================================================================
# PATTERN 3: LRU Memory Cache (Simple, Process-Local)
# ============================================================================


class LRUMemoryCachedStorage:
    """LRU in-memory cache wrapper for session storage.

    This strategy keeps recently used sessions in process memory using an
    LRU (Least Recently Used) eviction policy. Fastest possible cache,
    but not shared across processes/servers.

    Benefits:
    - Extremely fast (memory access)
    - No external dependencies
    - Simple implementation

    Limitations:
    - Not shared across servers/processes
    - Lost on process restart
    - Memory constrained

    Best for:
    - Single-server deployments
    - Development/testing
    - When you have spare memory

    Example:
        >>> from claude_agent_sdk.session_storage import S3SessionStorage, S3Config
        >>> backend = S3SessionStorage(S3Config(bucket="my-bucket"))
        >>> cached = LRUMemoryCachedStorage(backend, max_size=100)
        >>> options = ClaudeAgentOptions(session_storage=cached)
    """

    def __init__(self, backend: SessionStorage, max_size: int = 100):
        """Initialize LRU memory cache wrapper.

        Args:
            backend: Underlying storage backend.
            max_size: Maximum number of sessions to cache.
        """
        self.backend = backend
        self.max_size = max_size
        # Cache format: {session_id: (content_bytes, access_time)}
        self.cache: dict[str, tuple[bytes, float]] = {}
        logger.info(f"Initialized LRU memory cache with max_size={max_size}")

    def _evict_if_needed(self) -> None:
        """Evict least recently used item if cache is full."""
        if len(self.cache) >= self.max_size:
            # Find LRU item
            lru_session = min(self.cache.items(), key=lambda x: x[1][1])
            del self.cache[lru_session[0]]
            logger.debug(f"Evicted {lru_session[0]} from LRU cache")

    async def upload_transcript(self, session_id: str, local_path: Path | str) -> str:
        """Upload transcript and update cache."""
        # Upload to backend
        result = await self.backend.upload_transcript(session_id, local_path)

        # Update memory cache
        local_path = Path(local_path)
        if local_path.exists():
            self._evict_if_needed()
            content = local_path.read_bytes()
            self.cache[session_id] = (content, time.time())
            logger.debug(f"Cached transcript for {session_id} in memory")

        return result

    async def download_transcript(
        self, session_id: str, local_path: Path | str
    ) -> bool:
        """Download transcript, using memory cache if available."""
        local_path = Path(local_path)

        # Check memory cache first
        if session_id in self.cache:
            logger.info(f"Memory cache HIT for {session_id}")
            content, _ = self.cache[session_id]
            # Update access time
            self.cache[session_id] = (content, time.time())
            local_path.write_bytes(content)
            return True

        # Cache miss - fetch from backend
        logger.info(f"Memory cache MISS for {session_id}")
        success = await self.backend.download_transcript(session_id, local_path)

        if success:
            # Populate memory cache
            self._evict_if_needed()
            content = local_path.read_bytes()
            self.cache[session_id] = (content, time.time())
            logger.debug(f"Populated memory cache for {session_id}")

        return success

    async def exists(self, session_id: str) -> bool:
        """Check if session exists."""
        if session_id in self.cache:
            return True
        return await self.backend.exists(session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete from both cache and backend."""
        # Remove from memory
        self.cache.pop(session_id, None)
        # Remove from backend
        return await self.backend.delete(session_id)

    async def list_sessions(
        self, prefix: str | None = None, limit: int = 100
    ) -> list[SessionMetadata]:
        """List sessions from backend."""
        return await self.backend.list_sessions(prefix, limit)

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata from backend."""
        return await self.backend.get_metadata(session_id)

    def clear_cache(self) -> int:
        """Clear all cached sessions from memory."""
        count = len(self.cache)
        self.cache.clear()
        logger.info(f"Cleared {count} sessions from memory cache")
        return count

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "utilization": len(self.cache) / self.max_size,
        }


# ============================================================================
# Example Usage
# ============================================================================


async def example_file_cache():
    """Demonstrate local file cache."""
    print("=== Local File Cache Example ===\n")

    from claude_agent_sdk.session_storage import S3Config, S3SessionStorage

    # Create backend
    backend = S3SessionStorage(
        S3Config(bucket="my-sessions", prefix="claude", region="us-east-1")
    )

    # Wrap with file cache
    cached_storage = LocalFileCachedStorage(backend, cache_dir="/tmp/claude-cache")

    # Use with SDK
    options = ClaudeAgentOptions(session_storage=cached_storage)

    print("First request (cache miss) - will fetch from S3:")
    async with ClaudeSDKClient(options=options) as client:
        await client.query("What is 2 + 2?")
        async for msg in client.receive_response():
            display_message(msg)

    print("\nSecond request (cache hit) - will read from local cache:")
    async with ClaudeSDKClient(options=options) as client:
        await client.query("What is 3 + 3?")
        async for msg in client.receive_response():
            display_message(msg)

    # Cleanup
    cached_storage.clear_cache()
    print("\n")


async def example_redis_cache_pseudocode():
    """Show Redis cache pattern (pseudocode - requires Redis)."""
    print("=== Redis Cache Pattern (Pseudocode) ===\n")

    print("# Install dependencies:")
    print("pip install redis")
    print()
    print("# Code:")
    print("import redis.asyncio as redis")
    print("from claude_agent_sdk.session_storage import S3SessionStorage, S3Config")
    print()
    print("# Connect to Redis")
    print('redis_client = await redis.from_url("redis://localhost:6379")')
    print()
    print("# Create cached storage")
    print("backend = S3SessionStorage(S3Config(bucket='my-sessions'))")
    print("cached = RedisCachedStorage(")
    print("    backend=backend,")
    print("    redis_client=redis_client,")
    print("    ttl=3600,  # 1 hour TTL")
    print(")")
    print()
    print("# Use with SDK")
    print("options = ClaudeAgentOptions(session_storage=cached)")
    print()
    print("Benefits:")
    print("- Shared cache across all servers")
    print("- Automatic expiration (TTL)")
    print("- Sub-millisecond access times")
    print("- Production-proven reliability")
    print("\n")


async def example_lru_cache():
    """Demonstrate LRU memory cache."""
    print("=== LRU Memory Cache Example ===\n")

    print("Configuration example:")
    print()
    print("from claude_agent_sdk.session_storage import S3SessionStorage, S3Config")
    print()
    print("# Create backend")
    print("backend = S3SessionStorage(")
    print("    S3Config(bucket='my-sessions', prefix='claude', region='us-east-1')")
    print(")")
    print()
    print("# Wrap with LRU cache")
    print("cached_storage = LRUMemoryCachedStorage(backend, max_size=10)")
    print()
    print("# Use with SDK")
    print("options = ClaudeAgentOptions(session_storage=cached_storage)")
    print()
    print("Memory cache benefits:")
    print("  - Extremely fast (memory access)")
    print("  - No external dependencies")
    print("  - Simple implementation")
    print("  - Max size: configurable (default shown: 10 sessions)")
    print()
    print("Note: Perfect for single-server deployments or development.")
    print("\n")


async def example_cache_comparison():
    """Compare different caching strategies."""
    print("=== Caching Strategy Comparison ===\n")

    print("| Strategy     | Latency | Shared | Persistent | Complexity | Cost      |")
    print("|--------------|---------|--------|------------|------------|-----------|")
    print("| No Cache     | 50-500ms| N/A    | Yes        | Low        | API calls |")
    print("| File Cache   | ~1-5ms  | No     | Yes        | Low        | Disk      |")
    print("| Redis Cache  | ~0.1ms  | Yes    | Optional   | Medium     | Redis     |")
    print("| Memory Cache | ~0.01ms | No     | No         | Low        | RAM       |")
    print()
    print("Recommendations:")
    print()
    print("1. SINGLE SERVER / DEVELOPMENT:")
    print("   -> Use LocalFileCachedStorage or LRUMemoryCachedStorage")
    print("   -> Simple, no dependencies, good enough")
    print()
    print("2. PRODUCTION / MULTI-SERVER:")
    print("   -> Use RedisCachedStorage")
    print("   -> Shared cache, automatic eviction, proven at scale")
    print()
    print("3. SERVERLESS / CONTAINERS:")
    print("   -> Use Redis or external cache service")
    print("   -> File/memory caches reset on each invocation")
    print()
    print("4. LOW TRAFFIC (<100 req/min):")
    print("   -> Don't cache! Direct S3/GCS is fine")
    print("   -> Measure first, optimize if needed")
    print("\n")


async def example_custom_cache():
    """Show how to implement a custom cache strategy."""
    print("=== Custom Cache Implementation ===\n")

    print(
        "The SDK provides the SessionStorage protocol - implement it however you want:"
    )
    print()
    print("class MyCustomCache:")
    print('    """Your custom caching logic."""')
    print()
    print("    def __init__(self, backend, my_cache_system):")
    print("        self.backend = backend")
    print("        self.cache = my_cache_system")
    print()
    print("    async def upload_transcript(self, session_id, local_path):")
    print("        # Upload to backend")
    print(
        "        result = await self.backend.upload_transcript(session_id, local_path)"
    )
    print("        # Update your cache")
    print("        await self.cache.set(session_id, local_path)")
    print("        return result")
    print()
    print("    async def download_transcript(self, session_id, local_path):")
    print("        # Try cache first")
    print("        if await self.cache.has(session_id):")
    print("            return await self.cache.get(session_id, local_path)")
    print("        # Cache miss - fetch from backend")
    print(
        "        return await self.backend.download_transcript(session_id, local_path)"
    )
    print()
    print("    # ... implement other SessionStorage methods ...")
    print()
    print("Examples of custom caches:")
    print("- Memcached")
    print("- DynamoDB")
    print("- Cloudflare KV")
    print("- Your own distributed cache")
    print("\n")


async def main():
    """Run caching examples."""
    print("Claude Agent SDK - Session Storage Caching Patterns")
    print("=" * 60)
    print()

    # await example_file_cache()
    await example_redis_cache_pseudocode()
    await example_lru_cache()
    await example_cache_comparison()
    await example_custom_cache()

    print("=" * 60)
    print()
    print("Key Takeaways:")
    print()
    print("1. The SDK provides primitive storage implementations")
    print("2. You add caching tailored to your needs")
    print("3. Start simple, add caching when you measure latency problems")
    print("4. Redis is the production standard for distributed caching")
    print("5. File/memory caches work great for single-server deployments")
    print()


if __name__ == "__main__":
    # Set up logging to see cache hits/misses
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    asyncio.run(main())
