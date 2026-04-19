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

    def test_store_implements_is_canonical_probe(self) -> None:
        """SessionStore is intentionally not @runtime_checkable; capability
        detection goes through _store_implements()."""
        from claude_agent_sdk._internal.session_store_validation import (
            _store_implements,
        )

        store = InMemorySessionStore()
        assert _store_implements(store, "append")
        assert _store_implements(store, "list_sessions")
        with pytest.raises(TypeError):
            isinstance(store, SessionStore)

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

    def test_continue_with_resume_and_store_lacking_list_sessions(self) -> None:
        """Parity with TS: when resume is explicitly set, continue=True
        should not require list_sessions() — list_sessions is provably
        never called because resume wins.
        """

        class MinimalStore(SessionStore):
            async def append(self, key, entries):
                pass

            async def load(self, key):
                return None

        validate_session_store_options(
            ClaudeAgentOptions(
                session_store=MinimalStore(),
                continue_conversation=True,
                resume="00000000-0000-4000-8000-000000000000",
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

    def test_relative_dir_resolved_to_absolute_before_hashing(self) -> None:
        """Parity with TS: a relative dir like '.' must produce the
        absolute-path key the subprocess writes under, not a key derived
        from the literal '.' (which would silently miss store lookups)."""
        from pathlib import Path

        from claude_agent_sdk._internal.sessions import _sanitize_path

        key = project_key_for_directory(".")
        assert key == _sanitize_path(str(Path().resolve()))
        assert key != _sanitize_path(".")

    def test_nfc_normalizes_decomposed_unicode(self, tmp_path) -> None:
        """Parity with TS: the CLI canonicalizes via realpath + NFC. On macOS
        HFS+ a path like ``café`` may be stored decomposed (``cafe\\u0301``);
        without NFC normalization the SDK's project_key would mismatch the
        CLI's and store.load() would silently miss."""
        import unicodedata

        nfc = tmp_path / unicodedata.normalize("NFC", "café")
        nfd = tmp_path / unicodedata.normalize("NFD", "café")
        nfc.mkdir(exist_ok=True)
        assert project_key_for_directory(str(nfc)) == project_key_for_directory(
            str(nfd)
        )

    def test_long_path_uses_portable_djb2_suffix(self) -> None:
        """Parity with TS: paths > MAX_SANITIZED_LENGTH get a djb2 hash
        suffix (runtime-portable so parent and subprocess derive the
        same project_key)."""
        from pathlib import Path

        from claude_agent_sdk._internal.sessions import (
            MAX_SANITIZED_LENGTH,
            _simple_hash,
        )

        long_dir = str(Path("/" + "a" * (MAX_SANITIZED_LENGTH + 50)).resolve())
        key = project_key_for_directory(long_dir)
        portable_suffix = _simple_hash(long_dir)
        assert key.endswith("-" + portable_suffix)
        assert len(key) > MAX_SANITIZED_LENGTH
