"""Comprehensive unit tests for session storage module."""

from pathlib import Path

import pytest

from claude_agent_sdk import SessionStorageError
from claude_agent_sdk.session_storage import SessionMetadata, SessionSyncManager
from claude_agent_sdk.session_storage._base import BaseSessionStorage
from claude_agent_sdk.session_storage._protocol import SessionStorage

# ============================================================================
# Mock Implementation
# ============================================================================


class MockSessionStorage:
    """In-memory session storage for testing.

    Implements the SessionStorage protocol without requiring cloud services.
    """

    def __init__(self) -> None:
        """Initialize mock storage with in-memory data structures."""
        # Simulate cloud storage with dict: session_id -> (content bytes, metadata)
        self._storage: dict[str, tuple[bytes, SessionMetadata]] = {}

    async def upload_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> str:
        """Upload transcript to mock storage."""
        path = Path(local_path)
        if not path.exists():
            raise SessionStorageError(
                f"Local transcript not found: {path}",
                session_id=session_id,
                operation="upload",
            )

        content = path.read_bytes()
        import time

        now = time.time()

        # Create metadata
        metadata = SessionMetadata(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            size_bytes=len(content),
            storage_key=f"mock-storage/{session_id}/transcript.jsonl",
        )

        self._storage[session_id] = (content, metadata)
        return metadata.storage_key

    async def download_transcript(
        self,
        session_id: str,
        local_path: Path | str,
    ) -> bool:
        """Download transcript from mock storage."""
        if session_id not in self._storage:
            return False

        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content, _ = self._storage[session_id]
        path.write_bytes(content)
        return True

    async def exists(self, session_id: str) -> bool:
        """Check if session exists in mock storage."""
        return session_id in self._storage

    async def delete(self, session_id: str) -> bool:
        """Delete session from mock storage."""
        if session_id not in self._storage:
            return False
        del self._storage[session_id]
        return True

    async def list_sessions(
        self,
        prefix: str | None = None,
        limit: int = 100,
    ) -> list[SessionMetadata]:
        """List sessions in mock storage."""
        results = []
        for session_id, (_, metadata) in self._storage.items():
            if prefix is None or session_id.startswith(prefix):
                results.append(metadata)
                if len(results) >= limit:
                    break
        return results

    async def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Get metadata for a session."""
        if session_id not in self._storage:
            return None
        _, metadata = self._storage[session_id]
        return metadata


# ============================================================================
# Test SessionMetadata
# ============================================================================


class TestSessionMetadata:
    """Test SessionMetadata dataclass."""

    def test_create_metadata(self):
        """Test creating metadata with all fields."""
        metadata = SessionMetadata(
            session_id="session-123",
            created_at=1234567890.0,
            updated_at=1234567900.0,
            size_bytes=1024,
            storage_key="s3://bucket/session-123/transcript.jsonl",
        )

        assert metadata.session_id == "session-123"
        assert metadata.created_at == 1234567890.0
        assert metadata.updated_at == 1234567900.0
        assert metadata.size_bytes == 1024
        assert metadata.storage_key == "s3://bucket/session-123/transcript.jsonl"

    def test_metadata_equality(self):
        """Test metadata equality comparison."""
        metadata1 = SessionMetadata(
            session_id="session-123",
            created_at=1234567890.0,
            updated_at=1234567900.0,
            size_bytes=1024,
            storage_key="key1",
        )
        metadata2 = SessionMetadata(
            session_id="session-123",
            created_at=1234567890.0,
            updated_at=1234567900.0,
            size_bytes=1024,
            storage_key="key1",
        )

        assert metadata1 == metadata2

    def test_metadata_inequality(self):
        """Test metadata inequality."""
        metadata1 = SessionMetadata(
            session_id="session-123",
            created_at=1234567890.0,
            updated_at=1234567900.0,
            size_bytes=1024,
            storage_key="key1",
        )
        metadata2 = SessionMetadata(
            session_id="session-456",
            created_at=1234567890.0,
            updated_at=1234567900.0,
            size_bytes=1024,
            storage_key="key2",
        )

        assert metadata1 != metadata2


# ============================================================================
# Test Protocol Compliance
# ============================================================================


class TestProtocolCompliance:
    """Test that MockSessionStorage satisfies SessionStorage protocol."""

    def test_mock_storage_is_session_storage(self):
        """Test MockSessionStorage implements SessionStorage protocol."""
        storage = MockSessionStorage()
        assert isinstance(storage, SessionStorage)

    def test_mock_storage_has_all_methods(self):
        """Test MockSessionStorage has all required methods."""
        storage = MockSessionStorage()

        # Check all protocol methods exist
        assert hasattr(storage, "upload_transcript")
        assert hasattr(storage, "download_transcript")
        assert hasattr(storage, "exists")
        assert hasattr(storage, "delete")
        assert hasattr(storage, "list_sessions")
        assert hasattr(storage, "get_metadata")

        # Check they're callable
        assert callable(storage.upload_transcript)
        assert callable(storage.download_transcript)
        assert callable(storage.exists)
        assert callable(storage.delete)
        assert callable(storage.list_sessions)
        assert callable(storage.get_metadata)


# ============================================================================
# Test MockSessionStorage Functionality
# ============================================================================


class TestMockSessionStorage:
    """Test MockSessionStorage implementation."""

    @pytest.mark.asyncio
    async def test_upload_and_download(self, tmp_path):
        """Test uploading and downloading transcripts."""
        storage = MockSessionStorage()

        # Create a test file
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"message": "test"}\n')

        # Upload
        key = await storage.upload_transcript("session-123", transcript_path)
        assert "session-123" in key

        # Download
        download_path = tmp_path / "downloaded.jsonl"
        success = await storage.download_transcript("session-123", download_path)
        assert success is True
        assert download_path.exists()
        assert download_path.read_text() == '{"message": "test"}\n'

    @pytest.mark.asyncio
    async def test_upload_nonexistent_file(self):
        """Test uploading a file that doesn't exist."""
        storage = MockSessionStorage()

        with pytest.raises(SessionStorageError) as exc_info:
            await storage.upload_transcript("session-123", "/nonexistent/file.jsonl")

        assert "Local transcript not found" in str(exc_info.value)
        assert exc_info.value.session_id == "session-123"
        assert exc_info.value.operation == "upload"

    @pytest.mark.asyncio
    async def test_download_nonexistent_session(self, tmp_path):
        """Test downloading a session that doesn't exist."""
        storage = MockSessionStorage()

        download_path = tmp_path / "downloaded.jsonl"
        success = await storage.download_transcript("nonexistent", download_path)
        assert success is False
        assert not download_path.exists()

    @pytest.mark.asyncio
    async def test_exists(self, tmp_path):
        """Test checking if session exists."""
        storage = MockSessionStorage()

        # Should not exist initially
        exists = await storage.exists("session-123")
        assert exists is False

        # Upload a session
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test")
        await storage.upload_transcript("session-123", transcript_path)

        # Should exist now
        exists = await storage.exists("session-123")
        assert exists is True

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        """Test deleting sessions."""
        storage = MockSessionStorage()

        # Upload a session
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test")
        await storage.upload_transcript("session-123", transcript_path)

        # Delete it
        deleted = await storage.delete("session-123")
        assert deleted is True

        # Should not exist anymore
        exists = await storage.exists("session-123")
        assert exists is False

        # Deleting again should return False
        deleted = await storage.delete("session-123")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path):
        """Test listing sessions."""
        storage = MockSessionStorage()

        # Upload multiple sessions
        for i in range(5):
            path = tmp_path / f"transcript-{i}.jsonl"
            path.write_text(f"test {i}")
            await storage.upload_transcript(f"session-{i}", path)

        # List all sessions
        sessions = await storage.list_sessions()
        assert len(sessions) == 5
        assert all(isinstance(meta, SessionMetadata) for meta in sessions)

    @pytest.mark.asyncio
    async def test_list_sessions_with_prefix(self, tmp_path):
        """Test listing sessions with prefix filter."""
        storage = MockSessionStorage()

        # Upload sessions with different prefixes
        for prefix in ["prod", "dev", "test"]:
            for i in range(2):
                path = tmp_path / f"{prefix}-{i}.jsonl"
                path.write_text(f"{prefix} {i}")
                await storage.upload_transcript(f"{prefix}-session-{i}", path)

        # List with prefix
        sessions = await storage.list_sessions(prefix="prod")
        assert len(sessions) == 2
        assert all("prod" in meta.session_id for meta in sessions)

    @pytest.mark.asyncio
    async def test_list_sessions_with_limit(self, tmp_path):
        """Test listing sessions respects limit."""
        storage = MockSessionStorage()

        # Upload 10 sessions
        for i in range(10):
            path = tmp_path / f"transcript-{i}.jsonl"
            path.write_text(f"test {i}")
            await storage.upload_transcript(f"session-{i}", path)

        # List with limit
        sessions = await storage.list_sessions(limit=3)
        assert len(sessions) <= 3

    @pytest.mark.asyncio
    async def test_get_metadata(self, tmp_path):
        """Test getting metadata for a session."""
        storage = MockSessionStorage()

        # Upload a session
        transcript_path = tmp_path / "transcript.jsonl"
        content = "test content"
        transcript_path.write_text(content)
        await storage.upload_transcript("session-123", transcript_path)

        # Get metadata
        metadata = await storage.get_metadata("session-123")
        assert metadata is not None
        assert metadata.session_id == "session-123"
        assert metadata.size_bytes == len(content)
        assert metadata.created_at > 0
        assert metadata.updated_at > 0
        assert "session-123" in metadata.storage_key

    @pytest.mark.asyncio
    async def test_get_metadata_nonexistent(self):
        """Test getting metadata for nonexistent session."""
        storage = MockSessionStorage()

        metadata = await storage.get_metadata("nonexistent")
        assert metadata is None


# ============================================================================
# Test BaseSessionStorage
# ============================================================================


class ConcreteSessionStorage(BaseSessionStorage):
    """Concrete implementation of BaseSessionStorage for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.uploaded_keys: list[str] = []
        self.downloaded_keys: list[str] = []
        self._mock_storage: dict[str, bytes] = {}
        self._should_fail = False
        self._fail_count = 0

    def set_failure_mode(self, should_fail: bool, fail_count: int = 999):
        """Configure failure mode for testing retries."""
        self._should_fail = should_fail
        self._fail_count = fail_count

    async def _do_upload(self, key: str, local_path: Path) -> None:
        if self._should_fail and self._fail_count > 0:
            self._fail_count -= 1
            raise Exception("Mock upload failure")
        self.uploaded_keys.append(key)
        self._mock_storage[key] = local_path.read_bytes()

    async def _do_download(self, key: str, local_path: Path) -> bool:
        if self._should_fail and self._fail_count > 0:
            self._fail_count -= 1
            raise Exception("Mock download failure")
        self.downloaded_keys.append(key)
        if key in self._mock_storage:
            local_path.write_bytes(self._mock_storage[key])
            return True
        return False

    async def _do_exists(self, key: str) -> bool:
        return key in self._mock_storage

    async def _do_delete(self, key: str) -> bool:
        if key in self._mock_storage:
            del self._mock_storage[key]
            return True
        return False

    async def _do_list(self, prefix: str, limit: int) -> list[SessionMetadata]:
        results = []
        for key in self._mock_storage:
            if key.startswith(prefix):
                session_id = self._extract_session_id(key)
                if session_id:
                    results.append(
                        SessionMetadata(
                            session_id=session_id,
                            created_at=1234567890.0,
                            updated_at=1234567890.0,
                            size_bytes=len(self._mock_storage[key]),
                            storage_key=key,
                        )
                    )
                if len(results) >= limit:
                    break
        return results

    async def _do_get_metadata(self, key: str) -> SessionMetadata | None:
        if key not in self._mock_storage:
            return None
        session_id = self._extract_session_id(key)
        if not session_id:
            return None
        return SessionMetadata(
            session_id=session_id,
            created_at=1234567890.0,
            updated_at=1234567890.0,
            size_bytes=len(self._mock_storage[key]),
            storage_key=key,
        )


class TestBaseSessionStorage:
    """Test BaseSessionStorage abstract base class."""

    def test_initialization(self):
        """Test BaseSessionStorage initialization."""
        storage = ConcreteSessionStorage(
            prefix="test-sessions", max_retries=5, retry_delay=2.0
        )
        assert storage.prefix == "test-sessions"
        assert storage.max_retries == 5
        assert storage.retry_delay == 2.0

    def test_prefix_stripping(self):
        """Test that trailing slashes are removed from prefix."""
        storage = ConcreteSessionStorage(prefix="test-sessions/")
        assert storage.prefix == "test-sessions"

    def test_get_key(self):
        """Test key generation."""
        storage = ConcreteSessionStorage(prefix="claude-sessions")

        key = storage._get_key("session-123")
        assert key == "claude-sessions/session-123/transcript.jsonl"

    def test_get_key_sanitization(self):
        """Test key generation sanitizes session IDs."""
        storage = ConcreteSessionStorage(prefix="claude-sessions")

        # Test path traversal prevention
        # The implementation replaces: / -> _, \ -> _, .. -> _
        key = storage._get_key("../../../etc/passwd")
        # ".." becomes "_", "/" becomes "_", so "../../../etc/passwd" -> "______etc_passwd"
        assert key == "claude-sessions/______etc_passwd/transcript.jsonl"
        assert ".." not in key

        # Test various dangerous characters
        key = storage._get_key("session/with\\slashes")
        # "/" and "\" both become "_"
        assert key == "claude-sessions/session_with_slashes/transcript.jsonl"

    def test_extract_session_id(self):
        """Test session ID extraction from storage key."""
        storage = ConcreteSessionStorage(prefix="claude-sessions")

        session_id = storage._extract_session_id(
            "claude-sessions/session-123/transcript.jsonl"
        )
        assert session_id == "session-123"

    def test_extract_session_id_wrong_prefix(self):
        """Test session ID extraction with wrong prefix."""
        storage = ConcreteSessionStorage(prefix="claude-sessions")

        session_id = storage._extract_session_id(
            "wrong-prefix/session-123/transcript.jsonl"
        )
        assert session_id is None

    def test_extract_session_id_invalid_format(self):
        """Test session ID extraction with invalid format."""
        storage = ConcreteSessionStorage(prefix="claude-sessions")

        session_id = storage._extract_session_id("claude-sessions/")
        assert session_id == ""

    @pytest.mark.asyncio
    async def test_upload_success(self, tmp_path):
        """Test successful upload."""
        storage = ConcreteSessionStorage()
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test content")

        key = await storage.upload_transcript("session-123", transcript_path)
        assert key == "claude-sessions/session-123/transcript.jsonl"
        assert "claude-sessions/session-123/transcript.jsonl" in storage.uploaded_keys

    @pytest.mark.asyncio
    async def test_upload_nonexistent_file(self):
        """Test upload raises error for nonexistent file."""
        storage = ConcreteSessionStorage()

        with pytest.raises(SessionStorageError) as exc_info:
            await storage.upload_transcript("session-123", "/nonexistent/file.jsonl")

        assert "Local transcript not found" in str(exc_info.value)
        assert exc_info.value.session_id == "session-123"
        assert exc_info.value.operation == "upload"

    @pytest.mark.asyncio
    async def test_upload_retry_logic(self, tmp_path):
        """Test upload retry logic with exponential backoff."""
        storage = ConcreteSessionStorage(max_retries=3, retry_delay=0.01)
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test")

        # Fail twice, then succeed
        storage.set_failure_mode(True, fail_count=2)

        key = await storage.upload_transcript("session-123", transcript_path)
        assert key == "claude-sessions/session-123/transcript.jsonl"

    @pytest.mark.asyncio
    async def test_upload_retry_exhausted(self, tmp_path):
        """Test upload raises error after exhausting retries."""
        storage = ConcreteSessionStorage(max_retries=3, retry_delay=0.01)
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test")

        # Always fail
        storage.set_failure_mode(True, fail_count=999)

        with pytest.raises(SessionStorageError) as exc_info:
            await storage.upload_transcript("session-123", transcript_path)

        assert "Upload failed after 3 attempts" in str(exc_info.value)
        assert exc_info.value.session_id == "session-123"
        assert exc_info.value.operation == "upload"
        assert exc_info.value.original_error is not None

    @pytest.mark.asyncio
    async def test_download_success(self, tmp_path):
        """Test successful download."""
        storage = ConcreteSessionStorage()

        # First upload something
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test content")
        await storage.upload_transcript("session-123", upload_path)

        # Then download it
        download_path = tmp_path / "download.jsonl"
        success = await storage.download_transcript("session-123", download_path)
        assert success is True
        assert download_path.exists()
        assert download_path.read_text() == "test content"

    @pytest.mark.asyncio
    async def test_download_creates_parent_directory(self, tmp_path):
        """Test download creates parent directories."""
        storage = ConcreteSessionStorage()

        # Upload something
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test")
        await storage.upload_transcript("session-123", upload_path)

        # Download to nested path
        download_path = tmp_path / "nested" / "deep" / "download.jsonl"
        success = await storage.download_transcript("session-123", download_path)
        assert success is True
        assert download_path.exists()

    @pytest.mark.asyncio
    async def test_download_not_found(self, tmp_path):
        """Test download returns False when session not found."""
        storage = ConcreteSessionStorage()

        download_path = tmp_path / "download.jsonl"
        success = await storage.download_transcript("nonexistent", download_path)
        assert success is False

    @pytest.mark.asyncio
    async def test_download_retry_logic(self, tmp_path):
        """Test download retry logic."""
        storage = ConcreteSessionStorage(max_retries=3, retry_delay=0.01)

        # Upload first
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test")
        await storage.upload_transcript("session-123", upload_path)

        # Fail twice, then succeed
        storage.set_failure_mode(True, fail_count=2)

        download_path = tmp_path / "download.jsonl"
        success = await storage.download_transcript("session-123", download_path)
        assert success is True

    @pytest.mark.asyncio
    async def test_download_retry_exhausted(self, tmp_path):
        """Test download raises error after exhausting retries."""
        storage = ConcreteSessionStorage(max_retries=3, retry_delay=0.01)

        # Upload first
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test")
        await storage.upload_transcript("session-123", upload_path)

        # Always fail
        storage.set_failure_mode(True, fail_count=999)

        download_path = tmp_path / "download.jsonl"
        with pytest.raises(SessionStorageError) as exc_info:
            await storage.download_transcript("session-123", download_path)

        assert "Download failed after 3 attempts" in str(exc_info.value)
        assert exc_info.value.session_id == "session-123"
        assert exc_info.value.operation == "download"

    @pytest.mark.asyncio
    async def test_exists(self, tmp_path):
        """Test exists method."""
        storage = ConcreteSessionStorage()

        # Should not exist initially
        exists = await storage.exists("session-123")
        assert exists is False

        # Upload and check again
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test")
        await storage.upload_transcript("session-123", upload_path)

        exists = await storage.exists("session-123")
        assert exists is True

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        """Test delete method."""
        storage = ConcreteSessionStorage()

        # Upload first
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test")
        await storage.upload_transcript("session-123", upload_path)

        # Delete
        deleted = await storage.delete("session-123")
        assert deleted is True

        # Should not exist anymore
        exists = await storage.exists("session-123")
        assert exists is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        """Test deleting nonexistent session returns False."""
        storage = ConcreteSessionStorage()

        deleted = await storage.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path):
        """Test listing sessions."""
        storage = ConcreteSessionStorage()

        # Upload multiple sessions
        for i in range(5):
            path = tmp_path / f"transcript-{i}.jsonl"
            path.write_text(f"test {i}")
            await storage.upload_transcript(f"session-{i}", path)

        # List all
        sessions = await storage.list_sessions()
        assert len(sessions) == 5

    @pytest.mark.asyncio
    async def test_list_sessions_with_prefix(self, tmp_path):
        """Test listing sessions with prefix."""
        storage = ConcreteSessionStorage(prefix="test")

        # Upload sessions
        for i in range(3):
            path = tmp_path / f"transcript-{i}.jsonl"
            path.write_text(f"test {i}")
            await storage.upload_transcript(f"prod-{i}", path)

        # List with prefix
        sessions = await storage.list_sessions(prefix="prod")
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_get_metadata(self, tmp_path):
        """Test getting metadata."""
        storage = ConcreteSessionStorage()

        # Upload first
        upload_path = tmp_path / "upload.jsonl"
        upload_path.write_text("test content")
        await storage.upload_transcript("session-123", upload_path)

        # Get metadata
        metadata = await storage.get_metadata("session-123")
        assert metadata is not None
        assert metadata.session_id == "session-123"
        assert metadata.size_bytes > 0

    @pytest.mark.asyncio
    async def test_get_metadata_nonexistent(self):
        """Test getting metadata for nonexistent session."""
        storage = ConcreteSessionStorage()

        metadata = await storage.get_metadata("nonexistent")
        assert metadata is None


# ============================================================================
# Test SessionSyncManager
# ============================================================================


class TestSessionSyncManager:
    """Test SessionSyncManager for cloud storage synchronization."""

    def test_initialization_default_dir(self):
        """Test manager initialization with default directory."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage)

        assert manager.storage == storage
        assert manager.transcript_dir.exists()
        assert "claude-sessions" in str(manager.transcript_dir)

    def test_initialization_custom_dir(self, tmp_path):
        """Test manager initialization with custom directory."""
        storage = MockSessionStorage()
        custom_dir = tmp_path / "custom-transcripts"

        manager = SessionSyncManager(storage, transcript_dir=custom_dir)

        assert manager.storage == storage
        assert manager.transcript_dir == custom_dir
        assert custom_dir.exists()

    def test_get_local_transcript_path(self, tmp_path):
        """Test getting local transcript path."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        path = manager.get_local_transcript_path("session-123")
        assert path == tmp_path / "session-123" / "transcript.jsonl"
        assert path.parent.exists()

    def test_get_local_transcript_path_caching(self, tmp_path):
        """Test that local transcript paths are cached."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        path1 = manager.get_local_transcript_path("session-123")
        path2 = manager.get_local_transcript_path("session-123")
        assert path1 is path2  # Same object

    @pytest.mark.asyncio
    async def test_prepare_session_new(self, tmp_path):
        """Test preparing a new session (not in cloud)."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        path = await manager.prepare_session("session-123")
        assert path == tmp_path / "session-123" / "transcript.jsonl"
        assert not path.exists()  # New session, no file yet

    @pytest.mark.asyncio
    async def test_prepare_session_existing(self, tmp_path):
        """Test preparing an existing session (downloads from cloud)."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Upload a session to cloud first
        cloud_transcript = tmp_path / "cloud-transcript.jsonl"
        cloud_transcript.write_text('{"message": "from cloud"}\n')
        await storage.upload_transcript("session-123", cloud_transcript)

        # Prepare session (should download)
        local_path = await manager.prepare_session("session-123")
        assert local_path.exists()
        assert local_path.read_text() == '{"message": "from cloud"}\n'

    @pytest.mark.asyncio
    async def test_finalize_session_success(self, tmp_path):
        """Test finalizing session uploads to cloud."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Create a local transcript
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"message": "test"}\n')

        # Finalize (upload)
        await manager.finalize_session("session-123", transcript_path)

        # Verify uploaded to cloud
        assert await storage.exists("session-123")

    @pytest.mark.asyncio
    async def test_finalize_session_nonexistent_file(self, tmp_path, caplog):
        """Test finalizing with nonexistent transcript logs warning."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Finalize with nonexistent file
        await manager.finalize_session("session-123", tmp_path / "nonexistent.jsonl")

        # Should log warning but not raise
        assert "Transcript not found" in caplog.text

    @pytest.mark.asyncio
    async def test_finalize_session_upload_error(self, tmp_path, caplog):
        """Test finalize handles upload errors gracefully."""
        # Create storage that will fail on upload
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Patch upload to fail
        async def failing_upload(*args, **kwargs):
            raise Exception("Upload failed")

        storage.upload_transcript = failing_upload

        # Create a local transcript
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"message": "test"}\n')

        # Finalize should not raise, just log error
        await manager.finalize_session("session-123", transcript_path)

        assert "Failed to upload session" in caplog.text

    @pytest.mark.asyncio
    async def test_finalize_session_cleans_up_tracking(self, tmp_path):
        """Test finalize cleans up internal session tracking."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Track a session
        manager.get_local_transcript_path("session-123")
        assert "session-123" in manager._active_sessions

        # Create transcript and finalize
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text("test")
        await manager.finalize_session("session-123", transcript_path)

        # Should be cleaned up
        assert "session-123" not in manager._active_sessions

    @pytest.mark.asyncio
    async def test_create_stop_hook(self, tmp_path):
        """Test creating a stop hook callback."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        hook = manager.create_stop_hook()
        assert callable(hook)

    @pytest.mark.asyncio
    async def test_stop_hook_uploads_transcript(self, tmp_path):
        """Test stop hook uploads transcript."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Create transcript
        transcript_path = tmp_path / "transcript.jsonl"
        transcript_path.write_text('{"message": "test"}\n')

        # Create and call hook
        hook = manager.create_stop_hook()
        input_data = {
            "session_id": "session-123",
            "transcript_path": str(transcript_path),
        }
        result = await hook(input_data, None, {})

        # Should return empty dict
        assert result == {}

        # Should have uploaded
        assert await storage.exists("session-123")

    @pytest.mark.asyncio
    async def test_stop_hook_missing_session_id(self, tmp_path):
        """Test stop hook handles missing session_id gracefully."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        hook = manager.create_stop_hook()
        input_data = {"transcript_path": str(tmp_path / "transcript.jsonl")}
        result = await hook(input_data, None, {})

        # Should not raise, just return empty dict
        assert result == {}

    @pytest.mark.asyncio
    async def test_stop_hook_missing_transcript_path(self):
        """Test stop hook handles missing transcript_path gracefully."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage)

        hook = manager.create_stop_hook()
        input_data = {"session_id": "session-123"}
        result = await hook(input_data, None, {})

        # Should not raise, just return empty dict
        assert result == {}

    @pytest.mark.asyncio
    async def test_stop_hook_handles_errors(self, tmp_path, caplog):
        """Test stop hook handles errors without raising."""
        storage = MockSessionStorage()
        manager = SessionSyncManager(storage, transcript_dir=tmp_path)

        # Patch finalize_session to fail
        async def failing_finalize(*args, **kwargs):
            raise Exception("Finalize failed")

        manager.finalize_session = failing_finalize

        # Call hook
        hook = manager.create_stop_hook()
        input_data = {
            "session_id": "session-123",
            "transcript_path": str(tmp_path / "transcript.jsonl"),
        }
        result = await hook(input_data, None, {})

        # Should return empty dict and log error
        assert result == {}
        assert "Stop hook failed" in caplog.text


# ============================================================================
# Test SessionStorageError
# ============================================================================


class TestSessionStorageError:
    """Test SessionStorageError exception."""

    def test_basic_error(self):
        """Test basic error creation."""
        error = SessionStorageError("Upload failed")
        assert "Upload failed" in str(error)
        assert error.session_id is None
        assert error.operation is None
        assert error.original_error is None

    def test_error_with_session_id(self):
        """Test error with session ID."""
        error = SessionStorageError("Upload failed", session_id="session-123")
        assert "Upload failed" in str(error)
        assert "session-123" in str(error)
        assert error.session_id == "session-123"

    def test_error_with_operation(self):
        """Test error with operation."""
        error = SessionStorageError("Failed", operation="upload")
        assert "upload" in str(error)
        assert error.operation == "upload"

    def test_error_with_original_error(self):
        """Test error with original error."""
        original = Exception("Connection timeout")
        error = SessionStorageError("Upload failed", original_error=original)
        assert "Connection timeout" in str(error)
        assert error.original_error is original

    def test_error_with_all_fields(self):
        """Test error with all fields."""
        original = Exception("Network error")
        error = SessionStorageError(
            "Upload failed",
            session_id="session-123",
            operation="upload",
            original_error=original,
        )
        assert "Upload failed" in str(error)
        assert "session-123" in str(error)
        assert "upload" in str(error)
        assert "Network error" in str(error)
        assert error.session_id == "session-123"
        assert error.operation == "upload"
        assert error.original_error is original

    def test_error_is_exception(self):
        """Test SessionStorageError is an Exception."""
        error = SessionStorageError("Test")
        assert isinstance(error, Exception)

    def test_error_can_be_raised(self):
        """Test SessionStorageError can be raised and caught."""
        with pytest.raises(SessionStorageError) as exc_info:
            raise SessionStorageError("Test error", session_id="test-123")

        assert "Test error" in str(exc_info.value)
        assert exc_info.value.session_id == "test-123"
