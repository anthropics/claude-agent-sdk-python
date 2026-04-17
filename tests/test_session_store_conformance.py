"""Shared conformance test suite for :class:`SessionStore` adapters.

Call :func:`run_session_store_conformance` from a ``pytest.mark.asyncio``
test to assert the 13 behavioral contracts every adapter must satisfy.
Tests for optional methods (``list_sessions``, ``delete``, ``list_subkeys``)
are skipped when named in ``skip_optional`` or when the store raises
:class:`NotImplementedError` for that method.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

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

OptionalMethod = str  # "list_sessions" | "delete" | "list_subkeys"
_OPTIONAL_METHODS: frozenset[str] = frozenset(
    {"list_sessions", "delete", "list_subkeys"}
)

_KEY: SessionKey = {"project_key": "proj", "session_id": "sess"}


def _has_optional(
    store: SessionStore, method: OptionalMethod, skip_optional: frozenset[str]
) -> bool:
    """True if ``store`` supports ``method`` and it isn't explicitly skipped."""
    if method in skip_optional:
        return False
    impl = getattr(store, method, None)
    if impl is None:
        return False
    # Distinguish a real override from the Protocol's default that raises
    # NotImplementedError: an override lives on the instance's class, the
    # default lives on SessionStore itself.
    default = getattr(SessionStore, method, None)
    return getattr(type(store), method, None) is not default


async def run_session_store_conformance(
    make_store: Callable[[], SessionStore | Awaitable[SessionStore]],
    *,
    skip_optional: frozenset[str] = frozenset(),
) -> None:
    """Assert the 13 :class:`SessionStore` behavioral contracts.

    ``make_store`` is invoked once per contract to provide isolation. It may be
    sync or async. Contracts for optional methods (``list_sessions``,
    ``delete``, ``list_subkeys``) are skipped when named in ``skip_optional``
    or when the store does not override that method.
    """
    invalid = skip_optional - _OPTIONAL_METHODS
    assert not invalid, f"unknown optional methods in skip_optional: {invalid}"

    async def fresh() -> SessionStore:
        result = make_store()
        if hasattr(result, "__await__"):
            return await result  # type: ignore[misc]
        return result  # type: ignore[return-value]

    probe = await fresh()
    has_list_sessions = _has_optional(probe, "list_sessions", skip_optional)
    has_delete = _has_optional(probe, "delete", skip_optional)
    has_list_subkeys = _has_optional(probe, "list_subkeys", skip_optional)

    # --- Required: append + load -------------------------------------------

    # 1. append then load returns same entries in same order
    store = await fresh()
    await store.append(_KEY, [{"uuid": "b", "n": 1}, {"uuid": "a", "n": 2}])
    loaded = await store.load(_KEY)
    # Deep-equal is the contract; byte-equal serialization is intentionally
    # NOT checked (Postgres JSONB may reorder keys — SDK never byte-compares).
    assert loaded == [{"uuid": "b", "n": 1}, {"uuid": "a", "n": 2}]

    # 2. load unknown key returns None
    store = await fresh()
    assert await store.load({"project_key": "proj", "session_id": "nope"}) is None
    await store.append(_KEY, [{"uuid": "x", "n": 1}])
    assert await store.load({**_KEY, "subpath": "nope"}) is None

    # 3. multiple append calls preserve call order
    store = await fresh()
    await store.append(_KEY, [{"uuid": "z", "n": 1}])
    await store.append(_KEY, [{"uuid": "a", "n": 2}, {"uuid": "m", "n": 3}])
    await store.append(_KEY, [{"uuid": "b", "n": 4}])
    assert await store.load(_KEY) == [
        {"uuid": "z", "n": 1},
        {"uuid": "a", "n": 2},
        {"uuid": "m", "n": 3},
        {"uuid": "b", "n": 4},
    ]

    # 4. append([]) is a no-op
    store = await fresh()
    await store.append(_KEY, [{"uuid": "a", "n": 1}])
    await store.append(_KEY, [])
    assert await store.load(_KEY) == [{"uuid": "a", "n": 1}]

    # 5. subpath keys are stored independently of main
    store = await fresh()
    sub: SessionKey = {**_KEY, "subpath": "subagents/agent-1"}
    await store.append(_KEY, [{"uuid": "m", "n": 1}])
    await store.append(sub, [{"uuid": "s", "n": 1}])
    assert await store.load(_KEY) == [{"uuid": "m", "n": 1}]
    assert await store.load(sub) == [{"uuid": "s", "n": 1}]

    # 6. project_key isolation
    store = await fresh()
    await store.append({"project_key": "A", "session_id": "s1"}, [{"from": "A"}])
    await store.append({"project_key": "B", "session_id": "s1"}, [{"from": "B"}])
    assert await store.load({"project_key": "A", "session_id": "s1"}) == [{"from": "A"}]
    assert await store.load({"project_key": "B", "session_id": "s1"}) == [{"from": "B"}]
    if has_list_sessions:
        assert len(await store.list_sessions("A")) == 1
        assert len(await store.list_sessions("B")) == 1

    # --- Optional: list_sessions -------------------------------------------

    if has_list_sessions:
        # 7. list_sessions returns session_ids for project
        store = await fresh()
        await store.append({"project_key": "proj", "session_id": "a"}, [{"n": 1}])
        await store.append({"project_key": "proj", "session_id": "b"}, [{"n": 1}])
        await store.append({"project_key": "other", "session_id": "c"}, [{"n": 1}])
        sessions = await store.list_sessions("proj")
        assert sorted(s["session_id"] for s in sessions) == ["a", "b"]
        # mtime must be epoch-ms; >1e12 rules out epoch-seconds (≈2001 in ms).
        assert all(math.isfinite(s["mtime"]) and s["mtime"] > 1e12 for s in sessions)
        assert await store.list_sessions("never-appended-project") == []

        # 8. list_sessions excludes subagent subpaths
        store = await fresh()
        await store.append({"project_key": "proj", "session_id": "main"}, [{"n": 1}])
        await store.append(
            {
                "project_key": "proj",
                "session_id": "main",
                "subpath": "subagents/agent-1",
            },
            [{"n": 1}],
        )
        sessions = await store.list_sessions("proj")
        assert [s["session_id"] for s in sessions] == ["main"]

    # --- Optional: delete --------------------------------------------------

    if has_delete:
        # 9. delete main then load returns None
        store = await fresh()
        await store.delete({"project_key": "proj", "session_id": "never-written"})
        await store.append(_KEY, [{"n": 1}])
        await store.delete(_KEY)
        assert await store.load(_KEY) is None

        # 10. delete main cascades to subkeys
        store = await fresh()
        sub1: SessionKey = {**_KEY, "subpath": "subagents/agent-1"}
        sub2: SessionKey = {**_KEY, "subpath": "subagents/agent-2"}
        other: SessionKey = {"project_key": "proj", "session_id": "sess2"}
        other_proj: SessionKey = {
            "project_key": "other-proj",
            "session_id": _KEY["session_id"],
        }
        await store.append(_KEY, [{"n": 1}])
        await store.append(sub1, [{"n": 1}])
        await store.append(sub2, [{"n": 1}])
        await store.append(other, [{"n": 1}])
        await store.append(other_proj, [{"n": 1}])

        await store.delete(_KEY)

        assert await store.load(_KEY) is None
        assert await store.load(sub1) is None
        assert await store.load(sub2) is None
        loaded_other = await store.load(other)
        assert loaded_other is not None and len(loaded_other) == 1
        loaded_other_proj = await store.load(other_proj)
        assert loaded_other_proj is not None and len(loaded_other_proj) == 1
        if has_list_subkeys:
            assert await store.list_subkeys(_KEY) == []
        if has_list_sessions:
            listed = await store.list_sessions(_KEY["project_key"])
            assert _KEY["session_id"] not in [s["session_id"] for s in listed]

        # 11. delete with subpath removes only that subkey
        store = await fresh()
        await store.append(_KEY, [{"n": 1}])
        await store.append(sub1, [{"n": 1}])
        await store.append(sub2, [{"n": 1}])

        await store.delete(sub1)

        assert await store.load(sub1) is None
        loaded_sub2 = await store.load(sub2)
        assert loaded_sub2 is not None and len(loaded_sub2) == 1
        loaded_main = await store.load(_KEY)
        assert loaded_main is not None and len(loaded_main) == 1
        if has_list_subkeys:
            assert await store.list_subkeys(_KEY) == ["subagents/agent-2"]

    # --- Optional: list_subkeys --------------------------------------------

    if has_list_subkeys:
        # 12. list_subkeys returns subpaths
        store = await fresh()
        await store.append(_KEY, [{"n": 1}])
        await store.append({**_KEY, "subpath": "subagents/agent-1"}, [{"n": 1}])
        await store.append({**_KEY, "subpath": "subagents/agent-2"}, [{"n": 1}])
        await store.append(
            {
                "project_key": _KEY["project_key"],
                "session_id": "other-sess",
                "subpath": "subagents/agent-x",
            },
            [{"n": 1}],
        )
        subkeys = await store.list_subkeys(_KEY)
        assert sorted(subkeys) == ["subagents/agent-1", "subagents/agent-2"]
        assert "subagents/agent-x" not in subkeys

        # 13. list_subkeys excludes main transcript
        store = await fresh()
        await store.append(_KEY, [{"n": 1}])
        assert await store.list_subkeys(_KEY) == []
        assert (
            await store.list_subkeys(
                {"project_key": "proj", "session_id": "never-appended"}
            )
            == []
        )


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
