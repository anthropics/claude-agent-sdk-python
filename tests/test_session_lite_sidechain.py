"""Bounded sidechain classification from contiguous head/tail samples."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, mock_open, patch

import pytest

from claude_agent_sdk._internal.sessions import (
    LITE_READ_BUF_SIZE,
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
def test_tail_cannot_override_unknown_or_earlier_middle_flag(
    head_flag: bool | None,
) -> None:
    first = {"type": "summary", "summary": "synthetic session"}
    if head_flag is not None:
        first["isSidechain"] = head_flag
    # Head has no flag (or a known flag), the omitted middle says false, and
    # the sampled tail says true. Tail alone cannot establish first ordering.
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
    assert lite.sidechain is head_flag
    assert (_parse_session_info_from_lite("synthetic", lite) is None) is (
        head_flag is True
    )


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


def test_large_incomplete_head_record_cannot_be_classified_from_substring() -> None:
    text = '{"isSidechain":true,"padding":"' + "p" * 200000 + '"}\n'
    lite = _jsonl_to_lite(text, 0)
    assert lite.sidechain is None


def test_disk_read_budget_is_two_fixed_buffers() -> None:
    raw = transcript(200000, [True]).encode()
    opened = mock_open()
    opened.return_value.read.side_effect = [
        raw[:LITE_READ_BUF_SIZE],
        raw[-LITE_READ_BUF_SIZE:],
    ]
    stat = SimpleNamespace(st_size=len(raw), st_mtime=0)
    with (
        patch.object(Path, "open", opened),
        patch("claude_agent_sdk._internal.sessions.os.fstat", return_value=stat),
    ):
        lite = _read_session_lite(Path("synthetic.jsonl"))
    assert lite is not None
    assert opened.return_value.read.call_args_list == [
        call(LITE_READ_BUF_SIZE),
        call(LITE_READ_BUF_SIZE),
    ]
    opened.return_value.seek.assert_called_once_with(len(raw) - LITE_READ_BUF_SIZE)


def test_missing_flag_does_not_read_or_parse_unsampled_middle() -> None:
    assert _sampled_sidechain_flag(b'{"summary":"head"}\n', b"{}\n", 300000) is None
