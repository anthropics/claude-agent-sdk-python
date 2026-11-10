"""Exact sidechain classification with sampled and streaming read paths."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, mock_open, patch

import pytest

from claude_agent_sdk._internal.sessions import (
    LITE_READ_BUF_SIZE,
    _first_sidechain_flag,
    _jsonl_to_lite,
    _parse_session_info_from_lite,
    _read_session_lite,
    _sampled_sidechain_flag,
)


def transcript(prefix_size: int, flags: list[bool]) -> str:
    entries = [
        {"type": "summary", "summary": "synthetic session"},
        {"type": "user", "message": {"role": "user", "content": "first prompt"}},
        {"type": "assistant", "message": {"content": "p" * prefix_size}},
    ]
    entries.extend({"type": "user", "isSidechain": flag} for flag in flags)
    return "\n".join(json.dumps(entry) for entry in entries) + "\n"


@pytest.mark.parametrize("prefix_size", [0, 70000, 130000])
@pytest.mark.parametrize("flags", [[True], [False, True], [True, False]])
def test_disk_and_memory_find_first_flag_in_fully_sampled_file(
    tmp_path: Path, prefix_size: int, flags: list[bool]
) -> None:
    text = transcript(prefix_size, flags)
    assert len(text.encode()) <= 2 * LITE_READ_BUF_SIZE
    path = tmp_path / "session.jsonl"
    path.write_bytes(text.encode())
    disk = _read_session_lite(path)
    assert disk is not None
    memory = _jsonl_to_lite(text, 0)
    assert disk.sidechain is flags[0]
    assert memory.sidechain is flags[0]
    for lite in [disk, memory]:
        result = _parse_session_info_from_lite("synthetic", lite)
        assert (result is None) is flags[0]


def test_unicode_and_json_record_crossing_head_boundary(tmp_path: Path) -> None:
    # The first byte of a multibyte character is the last byte in the head.
    prefix = b'{"type":"summary","summary":"'
    padding = b"p" * (LITE_READ_BUF_SIZE - len(prefix) - 1)
    raw = prefix + padding + 'é","isSidechain":true}\n'.encode()
    assert raw[LITE_READ_BUF_SIZE - 1 : LITE_READ_BUF_SIZE + 1] == "é".encode()
    path = tmp_path / "session.jsonl"
    path.write_bytes(raw)
    disk = _read_session_lite(path)
    assert disk is not None and disk.sidechain is True
    assert _jsonl_to_lite(raw.decode(), 0).sidechain is True


@pytest.mark.parametrize("size", [65536, 65537, 131072])
def test_exact_sample_boundaries_accept_final_record_without_newline(size: int) -> None:
    suffix = b'","isSidechain":true}'
    prefix = b'{"summary":"'
    raw = prefix + b"p" * (size - len(prefix) - len(suffix)) + suffix
    lite = _jsonl_to_lite(raw.decode(), 0)
    assert lite.size == size
    assert lite.sidechain is True


@pytest.mark.parametrize("head_flag", [None, False, True])
def test_tail_cannot_override_earlier_middle_flag(
    tmp_path: Path,
    head_flag: bool | None,
) -> None:
    first = {"type": "summary", "summary": "synthetic session"}
    if head_flag is not None:
        first["isSidechain"] = head_flag
    # The middle says false, and the sampled tail says true. The reader must
    # inspect the missing records in order instead of trusting the tail.
    text = json.dumps(first) + "\n"
    text += (
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": "first prompt"}}
        )
        + "\n"
    )
    text += json.dumps({"padding": "p" * 70000}) + "\n"
    text += json.dumps({"isSidechain": False}) + "\n"
    text += json.dumps({"padding": "q" * 150000}) + "\n"
    text += json.dumps({"isSidechain": True}) + "\n"
    lite = _jsonl_to_lite(text, 0)
    assert lite.size > 2 * LITE_READ_BUF_SIZE
    expected = False if head_flag is None else head_flag
    path = tmp_path / "middle.jsonl"
    path.write_bytes(text.encode())
    disk = _read_session_lite(path)
    assert disk is not None
    assert lite.sidechain is expected
    assert disk.sidechain is expected
    assert (_parse_session_info_from_lite("synthetic", lite) is None) is expected


@pytest.mark.parametrize(
    "prefix",
    [
        '{"isSidechain":"true"}\n',
        '{"isSidechain":1}\n',
        '{"message":{"isSidechain":true}}\n',
        "not json\nnull\n[]\n",
    ],
)
def test_only_explicit_top_level_boolean_decides(prefix: str) -> None:
    text = prefix + '{"isSidechain":false}\n{"isSidechain":true}\n'
    assert _jsonl_to_lite(text, 0).sidechain is False


def test_escaped_key_and_whitespace_are_parsed() -> None:
    text = '{"summary":"synthetic", "isSide\\u0063hain" : true}\n'
    assert _jsonl_to_lite(text, 0).sidechain is True


def test_deeply_nested_invalid_record_does_not_break_listing() -> None:
    text = "[" * 2000 + "0" + "]" * 2000 + '\n{"isSidechain":true}\n'
    assert _jsonl_to_lite(text, 0).sidechain is True


def test_large_record_crossing_both_sample_boundaries(tmp_path: Path) -> None:
    text = '{"isSidechain":true,"padding":"' + "p" * 200000 + '"}\n'
    lite = _jsonl_to_lite(text, 0)
    assert lite.sidechain is True
    path = tmp_path / "large-record.jsonl"
    path.write_bytes(text.encode())
    disk = _read_session_lite(path)
    assert disk is not None and disk.sidechain is True


def test_head_flag_keeps_two_buffer_fast_path_without_streaming() -> None:
    raw = b'{"isSidechain":false}\n' + transcript(200000, [True]).encode()
    opened = mock_open()
    opened.return_value.read.side_effect = [
        raw[:LITE_READ_BUF_SIZE],
        raw[-LITE_READ_BUF_SIZE:],
    ]
    stat = SimpleNamespace(st_size=len(raw), st_mtime=0)
    opened.return_value.__iter__.side_effect = AssertionError("unexpected full scan")
    with (
        patch.object(Path, "open", opened),
        patch("claude_agent_sdk._internal.sessions.os.fstat", return_value=stat),
    ):
        lite = _read_session_lite(Path("synthetic.jsonl"))
    assert lite is not None
    assert lite.sidechain is False
    assert opened.return_value.read.call_args_list == [
        call(LITE_READ_BUF_SIZE),
        call(LITE_READ_BUF_SIZE),
    ]
    opened.return_value.seek.assert_called_once_with(len(raw) - LITE_READ_BUF_SIZE)


def test_missing_sample_flag_requires_streaming_for_exact_result() -> None:
    assert _sampled_sidechain_flag(b'{"summary":"head"}\n', b"{}\n", 300000) is None


@pytest.mark.parametrize("prefix_size", [65000, 70000, 130000, 200000, 1000000])
@pytest.mark.parametrize("flags", [[], [True], [False, True], [True, False]])
def test_all_sizes_match_fold_and_disk_memory_classification(
    tmp_path: Path, prefix_size: int, flags: list[bool]
) -> None:
    from claude_agent_sdk import fold_session_summary
    from claude_agent_sdk._internal.session_summary import summary_entry_to_sdk_info

    text = transcript(prefix_size, flags)
    entries = [json.loads(line) for line in text.splitlines()]
    key = {"session_id": "synthetic", "project_key": "synthetic-project"}
    folded = fold_session_summary(None, key, entries)
    expected = folded["data"].get("is_sidechain")
    path = tmp_path / "session.jsonl"
    path.write_bytes(text.encode())
    disk = _read_session_lite(path)
    assert disk is not None
    for lite in [disk, _jsonl_to_lite(text, 0)]:
        assert lite.sidechain is expected
        assert (_parse_session_info_from_lite("synthetic", lite) is None) == (
            summary_entry_to_sdk_info(folded, None) is None
        )


def test_stream_stops_at_first_explicit_boolean() -> None:
    def records():
        yield b'{"isSidechain":"not a boolean"}\n'
        yield b'{"isSidechain":false}\n'
        raise AssertionError("read beyond first explicit boolean")

    assert _first_sidechain_flag(records()) is False


def test_rewrite_is_reclassified_without_stale_cache(tmp_path: Path) -> None:
    path = tmp_path / "rewritten.jsonl"
    for flag in [True, False]:
        path.write_bytes(transcript(200000, [flag]).encode())
        lite = _read_session_lite(path)
        assert lite is not None and lite.sidechain is flag


@pytest.mark.anyio
@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize("use_summaries", [False, True])
async def test_public_disk_and_store_listing_lookup_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: bool | None,
    use_summaries: bool,
) -> None:
    from claude_agent_sdk import (
        InMemorySessionStore,
        get_session_info,
        get_session_info_from_store,
        list_sessions,
        list_sessions_from_store,
        project_key_for_directory,
    )
    from claude_agent_sdk._internal.sessions import _sanitize_path

    class FallbackStore(InMemorySessionStore):
        async def list_session_summaries(self, project_key):
            raise NotImplementedError

    directory = str(tmp_path / "project")
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    project = config / "projects" / _sanitize_path(directory)
    project.mkdir(parents=True)
    session_id = "11111111-1111-4111-8111-111111111111"
    flags = [] if flag is None else [flag, not flag]
    text = transcript(200000, flags)
    (project / f"{session_id}.jsonl").write_bytes(text.encode())
    entries = [json.loads(line) for line in text.splitlines()]
    store = InMemorySessionStore() if use_summaries else FallbackStore()
    await store.append(
        {"project_key": project_key_for_directory(directory), "session_id": session_id},
        entries,
    )
    expected = [] if flag is True else [session_id]
    disk = list_sessions(directory=directory, include_worktrees=False)
    stored = await list_sessions_from_store(store, directory=directory)
    assert [item.session_id for item in disk] == expected
    assert [item.session_id for item in stored] == expected
    assert (get_session_info(session_id, directory=directory) is None) is (flag is True)
    stored_info = await get_session_info_from_store(
        store, session_id, directory=directory
    )
    assert (stored_info is None) is (flag is True)
