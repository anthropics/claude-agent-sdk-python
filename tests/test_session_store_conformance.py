"""Exercises the shipped :func:`run_session_store_conformance` harness against
:class:`InMemorySessionStore`, plus options-validation and project-key tests.

The harness itself lives in ``claude_agent_sdk.testing`` so third-party
adapter authors can import it.
"""

from __future__ import annotations

import pytest

from claude_agent_sdk import (
    ClaudeAgentOptions,
    InMemorySessionStore,
    SessionKey,
    SessionStore,
    project_key_for_directory,
)
from claude_agent_sdk._internal.session_store_validation import (
    validate_session_store_options,
)
from claude_agent_sdk.testing import run_session_store_conformance

_KEY: SessionKey = {"project_key": "proj", "session_id": "sess"}


# ---------------------------------------------------------------------------
# InMemorySessionStore tests
# ---------------------------------------------------------------------------


class TestInMemorySessionStore:
    @pytest.mark.asyncio
    async def test_conformance(self) -> None:
        await run_session_store_conformance(InMemorySessionStore)

    @pytest.mark.asyncio
    async def test_conformance_with_async_factory(self) -> None:
        async def make() -> SessionStore:
            return InMemorySessionStore()

        await run_session_store_conformance(make)

    @pytest.mark.asyncio
    async def test_skip_optional_suppresses_contracts(self) -> None:
        """A store implementing only required methods passes when optionals are skipped."""

        class MinimalStore:
            def __init__(self) -> None:
                self._data: dict[str, list] = {}

            async def append(self, key: SessionKey, entries: list) -> None:
                k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath') or ''}"
                self._data.setdefault(k, []).extend(entries)

            async def load(self, key: SessionKey) -> list | None:
                k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath') or ''}"
                return self._data.get(k)

        await run_session_store_conformance(
            MinimalStore,
            skip_optional=frozenset({"list_sessions", "delete", "list_subkeys"}),
        )

    @pytest.mark.asyncio
    async def test_auto_skips_unimplemented_optionals(self) -> None:
        """Optional contracts auto-skip when the store doesn't override them."""

        class MinimalStore(SessionStore):
            def __init__(self) -> None:
                self._data: dict[str, list] = {}

            async def append(self, key: SessionKey, entries: list) -> None:
                k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath') or ''}"
                self._data.setdefault(k, []).extend(entries)

            async def load(self, key: SessionKey) -> list | None:
                k = f"{key['project_key']}/{key['session_id']}/{key.get('subpath') or ''}"
                return self._data.get(k)

        # No skip_optional passed — auto-probe should detect missing overrides.
        await run_session_store_conformance(MinimalStore)

    def test_isinstance_runtime_checkable(self) -> None:
        assert isinstance(InMemorySessionStore(), SessionStore)

    @pytest.mark.asyncio
    async def test_get_entries_helper(self) -> None:
        store = InMemorySessionStore()
        assert store.get_entries(_KEY) == []
        await store.append(_KEY, [{"n": 1}, {"n": 2}])
        assert store.get_entries(_KEY) == [{"n": 1}, {"n": 2}]
        # Returns a copy — mutating the result must not affect the store.
        store.get_entries(_KEY).append({"n": 999})
        assert store.get_entries(_KEY) == [{"n": 1}, {"n": 2}]

    @pytest.mark.asyncio
    async def test_size_helper_counts_main_transcripts_only(self) -> None:
        store = InMemorySessionStore()
        assert store.size == 0
        await store.append({"project_key": "p", "session_id": "a"}, [{"n": 1}])
        await store.append({"project_key": "p", "session_id": "b"}, [{"n": 1}])
        await store.append(
            {"project_key": "p", "session_id": "a", "subpath": "sub/x"}, [{"n": 1}]
        )
        assert store.size == 2

    @pytest.mark.asyncio
    async def test_clear_helper(self) -> None:
        store = InMemorySessionStore()
        await store.append(_KEY, [{"n": 1}])
        await store.append({**_KEY, "subpath": "sub/x"}, [{"n": 1}])
        store.clear()
        assert store.size == 0
        assert await store.load(_KEY) is None
        assert await store.list_sessions("proj") == []

    @pytest.mark.asyncio
    async def test_load_returns_copy(self) -> None:
        store = InMemorySessionStore()
        await store.append(_KEY, [{"n": 1}])
        loaded = await store.load(_KEY)
        assert loaded is not None
        loaded.append({"n": 999})
        assert await store.load(_KEY) == [{"n": 1}]


# ---------------------------------------------------------------------------
# Options validation
# ---------------------------------------------------------------------------


class TestSessionStoreOptionsValidation:
    def test_no_store_is_always_valid(self) -> None:
        validate_session_store_options(
            ClaudeAgentOptions(
                continue_conversation=True, enable_file_checkpointing=True
            )
        )

    def test_valid_store_passes(self) -> None:
        validate_session_store_options(
            ClaudeAgentOptions(session_store=InMemorySessionStore())
        )

    def test_continue_conversation_requires_list_sessions(self) -> None:
        class MinimalStore(SessionStore):
            async def append(self, key, entries):
                pass

            async def load(self, key):
                return None

        with pytest.raises(ValueError, match="list_sessions"):
            validate_session_store_options(
                ClaudeAgentOptions(
                    session_store=MinimalStore(), continue_conversation=True
                )
            )

    def test_continue_conversation_ok_when_store_implements_list_sessions(
        self,
    ) -> None:
        # InMemorySessionStore implements list_sessions — should pass.
        validate_session_store_options(
            ClaudeAgentOptions(
                session_store=InMemorySessionStore(), continue_conversation=True
            )
        )

    def test_rejects_file_checkpointing_combo(self) -> None:
        with pytest.raises(ValueError, match="enable_file_checkpointing"):
            validate_session_store_options(
                ClaudeAgentOptions(
                    session_store=InMemorySessionStore(),
                    enable_file_checkpointing=True,
                )
            )


class TestProjectKeyForDirectory:
    def test_defaults_to_cwd(self) -> None:
        from pathlib import Path

        assert project_key_for_directory() == project_key_for_directory(Path.cwd())

    def test_sanitizes_path(self) -> None:
        key = project_key_for_directory("/tmp/my project!")
        assert "/" not in key
        assert " " not in key
        assert "!" not in key

    def test_stable_for_same_path(self) -> None:
        assert project_key_for_directory("/a/b/c") == project_key_for_directory(
            "/a/b/c"
        )
