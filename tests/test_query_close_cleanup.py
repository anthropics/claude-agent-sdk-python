"""Regression test for issue #886.

`Query.close()` did not guard the pre-transport cleanup with try/finally,
so if any earlier cleanup step raised, ``transport.close()`` would never
run — leaking the CLI subprocess and the stderr reader task.
"""

from unittest.mock import AsyncMock, Mock

import anyio
import pytest

from claude_agent_sdk._internal.query import Query


def _make_mock_transport():
    """Mock transport with awaitable lifecycle methods and an empty read stream."""
    mock_transport = AsyncMock()

    async def mock_receive():
        if False:  # pragma: no cover - empty generator
            yield {}

    mock_transport.read_messages = mock_receive
    mock_transport.connect = AsyncMock()
    mock_transport.close = AsyncMock()
    mock_transport.end_input = AsyncMock()
    mock_transport.write = AsyncMock()
    mock_transport.is_ready = Mock(return_value=True)
    return mock_transport


class _RaisingMirrorBatcher:
    """Minimal stand-in for TranscriptMirrorBatcher whose close() raises.

    Only ``close()`` is called from ``Query.close()``; the other methods are
    here so the type-stub set_transcript_mirror_batcher accepts it.
    """

    async def close(self) -> None:
        raise RuntimeError("simulated mirror-batcher failure")

    async def flush(self) -> None:  # pragma: no cover - not exercised
        return None

    def enqueue(self, file_path, entries) -> None:  # pragma: no cover
        return None


def test_close_runs_transport_close_when_mirror_batcher_close_raises():
    """transport.close() must still run when an earlier cleanup step raises.

    Regression for issue #886: previously, a raise from
    _transcript_mirror_batcher.close() short-circuited the rest of close()
    and skipped transport.close(), leaking the CLI subprocess.
    """

    async def _test():
        mock_transport = _make_mock_transport()
        q = Query(transport=mock_transport, is_streaming_mode=True)
        q.set_transcript_mirror_batcher(_RaisingMirrorBatcher())  # type: ignore[arg-type]

        await q.start()

        with pytest.raises(RuntimeError, match="simulated mirror-batcher failure"):
            await q.close()

        # The whole point of the fix: transport.close() must run even when
        # an earlier cleanup step raised.
        mock_transport.close.assert_called_once()

    anyio.run(_test)
