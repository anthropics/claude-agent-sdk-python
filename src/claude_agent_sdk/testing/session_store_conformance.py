"""Shared conformance test suite for :class:`SessionStore` adapters.

Call :func:`run_session_store_conformance` from an async test to assert the
13 behavioral contracts every adapter must satisfy. Tests for optional
methods (``list_sessions``, ``delete``, ``list_subkeys``) are skipped when
named in ``skip_optional`` or when the store does not override that method.

Example::

    import pytest
    from claude_agent_sdk.testing import run_session_store_conformance

    @pytest.mark.asyncio
    async def test_my_store_conformance():
        await run_session_store_conformance(MyRedisStore)
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable
from typing import Any

from ..types import SessionKey, SessionStore

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
        if inspect.isawaitable(result):
            return await result
        return result

    probe = await fresh()
    has_list_sessions = _has_optional(probe, "list_sessions", skip_optional)
    has_delete = _has_optional(probe, "delete", skip_optional)
    has_list_subkeys = _has_optional(probe, "list_subkeys", skip_optional)

    # --- Required: append + load -------------------------------------------

    # 1. append then load returns same entries in same order
    store = await fresh()
    await store.append(_KEY, [_e({"uuid": "b", "n": 1}), _e({"uuid": "a", "n": 2})])
    loaded = await store.load(_KEY)
    # Deep-equal is the contract; byte-equal serialization is intentionally
    # NOT checked (Postgres JSONB may reorder keys — SDK never byte-compares).
    assert loaded == [_e({"uuid": "b", "n": 1}), _e({"uuid": "a", "n": 2})]

    # 2. load unknown key returns None
    store = await fresh()
    assert await store.load({"project_key": "proj", "session_id": "nope"}) is None
    await store.append(_KEY, [_e({"uuid": "x", "n": 1})])
    assert await store.load({**_KEY, "subpath": "nope"}) is None

    # 3. multiple append calls preserve call order
    store = await fresh()
    await store.append(_KEY, [_e({"uuid": "z", "n": 1})])
    await store.append(_KEY, [_e({"uuid": "a", "n": 2}), _e({"uuid": "m", "n": 3})])
    await store.append(_KEY, [_e({"uuid": "b", "n": 4})])
    assert await store.load(_KEY) == [
        _e({"uuid": "z", "n": 1}),
        _e({"uuid": "a", "n": 2}),
        _e({"uuid": "m", "n": 3}),
        _e({"uuid": "b", "n": 4}),
    ]

    # 4. append([]) is a no-op
    store = await fresh()
    await store.append(_KEY, [_e({"uuid": "a", "n": 1})])
    await store.append(_KEY, [])
    assert await store.load(_KEY) == [_e({"uuid": "a", "n": 1})]

    # 5. subpath keys are stored independently of main
    store = await fresh()
    sub: SessionKey = {**_KEY, "subpath": "subagents/agent-1"}
    await store.append(_KEY, [_e({"uuid": "m", "n": 1})])
    await store.append(sub, [_e({"uuid": "s", "n": 1})])
    assert await store.load(_KEY) == [_e({"uuid": "m", "n": 1})]
    assert await store.load(sub) == [_e({"uuid": "s", "n": 1})]

    # 6. project_key isolation
    store = await fresh()
    await store.append({"project_key": "A", "session_id": "s1"}, [_e({"from": "A"})])
    await store.append({"project_key": "B", "session_id": "s1"}, [_e({"from": "B"})])
    assert await store.load({"project_key": "A", "session_id": "s1"}) == [
        _e({"from": "A"})
    ]
    assert await store.load({"project_key": "B", "session_id": "s1"}) == [
        _e({"from": "B"})
    ]
    if has_list_sessions:
        assert len(await store.list_sessions("A")) == 1
        assert len(await store.list_sessions("B")) == 1

    # --- Optional: list_sessions -------------------------------------------

    if has_list_sessions:
        # 7. list_sessions returns session_ids for project
        store = await fresh()
        await store.append({"project_key": "proj", "session_id": "a"}, [_e({"n": 1})])
        await store.append({"project_key": "proj", "session_id": "b"}, [_e({"n": 1})])
        await store.append({"project_key": "other", "session_id": "c"}, [_e({"n": 1})])
        sessions = await store.list_sessions("proj")
        assert sorted(s["session_id"] for s in sessions) == ["a", "b"]
        # mtime must be epoch-ms; >1e12 rules out epoch-seconds (≈2001 in ms).
        assert all(math.isfinite(s["mtime"]) and s["mtime"] > 1e12 for s in sessions)
        assert await store.list_sessions("never-appended-project") == []

        # 8. list_sessions excludes subagent subpaths
        store = await fresh()
        await store.append(
            {"project_key": "proj", "session_id": "main"}, [_e({"n": 1})]
        )
        await store.append(
            {
                "project_key": "proj",
                "session_id": "main",
                "subpath": "subagents/agent-1",
            },
            [_e({"n": 1})],
        )
        sessions = await store.list_sessions("proj")
        assert [s["session_id"] for s in sessions] == ["main"]

    # --- Optional: delete --------------------------------------------------

    if has_delete:
        # 9. delete main then load returns None
        store = await fresh()
        await store.delete({"project_key": "proj", "session_id": "never-written"})
        await store.append(_KEY, [_e({"n": 1})])
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
        await store.append(_KEY, [_e({"n": 1})])
        await store.append(sub1, [_e({"n": 1})])
        await store.append(sub2, [_e({"n": 1})])
        await store.append(other, [_e({"n": 1})])
        await store.append(other_proj, [_e({"n": 1})])

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
        await store.append(_KEY, [_e({"n": 1})])
        await store.append(sub1, [_e({"n": 1})])
        await store.append(sub2, [_e({"n": 1})])

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
        await store.append(_KEY, [_e({"n": 1})])
        await store.append({**_KEY, "subpath": "subagents/agent-1"}, [_e({"n": 1})])
        await store.append({**_KEY, "subpath": "subagents/agent-2"}, [_e({"n": 1})])
        await store.append(
            {
                "project_key": _KEY["project_key"],
                "session_id": "other-sess",
                "subpath": "subagents/agent-x",
            },
            [_e({"n": 1})],
        )
        subkeys = await store.list_subkeys(_KEY)
        assert sorted(subkeys) == ["subagents/agent-1", "subagents/agent-2"]
        assert "subagents/agent-x" not in subkeys

        # 13. list_subkeys excludes main transcript
        store = await fresh()
        await store.append(_KEY, [_e({"n": 1})])
        assert await store.list_subkeys(_KEY) == []
        assert (
            await store.list_subkeys(
                {"project_key": "proj", "session_id": "never-appended"}
            )
            == []
        )


def _e(d: dict[str, Any]) -> Any:
    """Build a test entry satisfying ``SessionStoreEntry`` (``type`` is required).

    Adapters must treat entries as opaque pass-through blobs; the value of
    ``type`` is irrelevant to the contracts under test.
    """
    return {"type": "x", **d}
