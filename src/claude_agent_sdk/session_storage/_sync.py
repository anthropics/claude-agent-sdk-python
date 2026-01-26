"""Session sync manager for coordinating cloud storage with CLI.

This module provides the SessionSyncManager which handles:
- Downloading sessions from cloud storage on resume
- Uploading sessions to cloud storage on session end (via Stop hook)
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._protocol import SessionStorage

logger = logging.getLogger(__name__)


class SessionSyncManager:
    """Manages transcript synchronization between local filesystem and cloud storage.

    This manager coordinates session persistence by:
    1. Downloading existing sessions from cloud storage when resuming
    2. Uploading session transcripts to cloud storage when sessions end

    The sync manager integrates with ClaudeSDKClient via hooks - it creates
    a Stop hook callback that automatically uploads transcripts when sessions end.

    WARNING: Cloud storage operations add latency. For production scale,
    consider wrapping your SessionStorage with a caching layer.

    Attributes:
        storage: The session storage backend (S3, GCS, or custom).
        transcript_dir: Local directory for transcript files.

    Example:
        Basic usage (typically handled by ClaudeSDKClient automatically):

        >>> from claude_agent_sdk.session_storage import SessionSyncManager, S3SessionStorage, S3Config
        >>>
        >>> storage = S3SessionStorage(S3Config(bucket="my-sessions"))
        >>> manager = SessionSyncManager(storage)
        >>>
        >>> # Prepare for session resume (downloads from cloud if exists)
        >>> local_path = await manager.prepare_session("session-123")
        >>>
        >>> # Create Stop hook for automatic upload
        >>> hook = manager.create_stop_hook()

    Note:
        In most cases, you don't need to use SessionSyncManager directly.
        Simply pass `session_storage` to ClaudeAgentOptions and the SDK
        handles sync automatically.
    """

    def __init__(
        self,
        storage: SessionStorage,
        transcript_dir: Path | str | None = None,
    ) -> None:
        """Initialize session sync manager.

        Args:
            storage: The session storage backend.
            transcript_dir: Directory for local transcripts. If None, uses
                a subdirectory in the system temp directory.
        """
        self.storage = storage
        if transcript_dir is None:
            self.transcript_dir = Path(tempfile.gettempdir()) / "claude-sessions"
        else:
            self.transcript_dir = Path(transcript_dir)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self._active_sessions: dict[str, Path] = {}

    def get_local_transcript_path(self, session_id: str) -> Path:
        """Get or create local path for a session's transcript.

        Args:
            session_id: The session identifier.

        Returns:
            Path where the transcript will be stored locally.
        """
        if session_id not in self._active_sessions:
            path = self.transcript_dir / session_id / "transcript.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._active_sessions[session_id] = path
        return self._active_sessions[session_id]

    async def prepare_session(self, session_id: str) -> Path:
        """Prepare local environment for a session.

        If the session exists in cloud storage, downloads it to local path.
        Called before session starts when resuming.

        Args:
            session_id: The session identifier.

        Returns:
            Local path where transcript will be/is stored.
        """
        local_path = self.get_local_transcript_path(session_id)

        # Try to download existing transcript from cloud
        if await self.storage.exists(session_id):
            logger.info(f"Downloading session {session_id} from cloud storage")
            downloaded = await self.storage.download_transcript(session_id, local_path)
            if downloaded:
                logger.info(f"Session {session_id} downloaded to {local_path}")
            else:
                logger.warning(f"Session {session_id} exists but download failed")
        else:
            logger.debug(
                f"Session {session_id} not found in cloud storage (new session)"
            )

        return local_path

    async def finalize_session(
        self, session_id: str, transcript_path: str | Path
    ) -> None:
        """Upload session transcript to cloud after session ends.

        Called from Stop hook.

        Args:
            session_id: The session identifier.
            transcript_path: Path to the transcript file (from hook input).
        """
        path = Path(transcript_path)

        if not path.exists():
            logger.warning(f"Transcript not found at {path}, skipping upload")
            return

        logger.info(f"Uploading session {session_id} to cloud storage")
        try:
            key = await self.storage.upload_transcript(session_id, path)
            logger.info(f"Session {session_id} uploaded to {key}")
        except Exception as e:
            # Log but don't fail - we don't want to break session end
            logger.error(f"Failed to upload session {session_id}: {e}")

        # Clean up tracking
        self._active_sessions.pop(session_id, None)

    def create_stop_hook(self) -> Any:
        """Create a Stop hook callback for automatic transcript upload.

        The returned callback uploads the session transcript to cloud storage
        when the session ends. Errors are logged but don't fail the session.

        Returns:
            HookCallback function for use with HookMatcher.

        Example:
            >>> manager = SessionSyncManager(storage)
            >>> hook = manager.create_stop_hook()
            >>>
            >>> # Use with HookMatcher
            >>> from claude_agent_sdk import HookMatcher
            >>> matcher = HookMatcher(matcher=None, hooks=[hook])
        """

        async def stop_hook(
            input_data: dict[str, Any],
            tool_use_id: str | None,
            context: dict[str, Any],
        ) -> dict[str, Any]:
            """Hook callback to upload transcript on session end."""
            session_id = input_data.get("session_id", "")
            transcript_path = input_data.get("transcript_path", "")

            if session_id and transcript_path:
                try:
                    await self.finalize_session(session_id, transcript_path)
                except Exception as e:
                    # Log error but don't fail the session
                    logger.error(f"Stop hook failed for session {session_id}: {e}")

            # Return empty output - don't modify session behavior
            return {}

        return stop_hook
