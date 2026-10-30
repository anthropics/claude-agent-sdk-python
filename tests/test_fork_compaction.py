"""Compaction reference integrity through both public fork APIs."""

import copy
import json
import uuid

import pytest

from claude_agent_sdk import (
    InMemorySessionStore,
    fork_session,
    fork_session_via_store,
    project_key_for_directory,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["disk", "store"])
def fork_entries(request, tmp_path, monkeypatch):
    directory = str(tmp_path / "project")
    project_key = project_key_for_directory(directory)
    config = tmp_path / "config"
    project = config / "projects" / project_key
    project.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    async def fork(rows, cutoff=None):
        sid = rows[0]["sessionId"]
        original = copy.deepcopy(rows)
        if request.param == "disk":
            source = project / f"{sid}.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            before = source.read_bytes()
            result = fork_session(sid, directory=directory, up_to_message_id=cutoff)
            copied = [
                json.loads(line)
                for line in (project / f"{result.session_id}.jsonl")
                .read_text()
                .splitlines()
            ]
            assert source.read_bytes() == before
        else:
            store = InMemorySessionStore()
            key = {"project_key": project_key, "session_id": sid}
            await store.append(key, rows)
            result = await fork_session_via_store(
                store, sid, directory=directory, up_to_message_id=cutoff
            )
            copied = await store.load(
                {"project_key": project_key, "session_id": result.session_id}
            )
            assert await store.load(key) == original
        assert rows == original
        return [row for row in copied if "forkedFrom" in row]

    return fork


def _transcript():
    sid, opener, kept, boundary, anchor, excluded = [
        str(uuid.uuid4()) for _ in range(6)
    ]
    rows = [
        {
            "type": "user",
            "uuid": uid,
            "parentUuid": parent,
            "sessionId": sid,
            "message": {"role": "user", "content": uid},
        }
        for uid, parent in [
            (opener, None),
            (kept, opener),
            (boundary, None),
            (anchor, boundary),
            (excluded, anchor),
        ]
    ]
    rows[2].update(
        type="system",
        subtype="compact_boundary",
        logicalParentUuid=kept,
        compactMetadata={
            "trigger": "auto",
            "preservedSegment": {
                "headUuid": opener,
                "tailUuid": kept,
                "anchorUuid": anchor,
            },
            "preservedMessages": {
                "anchorUuid": anchor,
                "uuids": [opener, kept],
                "allUuids": [opener, kept],
            },
            "runningTurnSends": {
                "openerUuid": opener,
                "uuids": [opener],
                "summarisedOnly": [kept],
            },
        },
    )
    rows[3]["isCompactSummary"] = True
    return rows


def _metadata_and_mapping(copied):
    metadata = next(
        row["compactMetadata"]
        for row in copied
        if row.get("subtype") == "compact_boundary"
    )
    mapping = {row["forkedFrom"]["messageUuid"]: row["uuid"] for row in copied}
    # Check the emitted graph, not the builder's internal mapping. Only these
    # schema fields are references; UUID-shaped extension data is not one.
    written = set(mapping.values())
    for row in copied:
        for field in ("parentUuid", "logicalParentUuid"):
            assert row.get(field) is None or row[field] in written
    for name, fields in (
        ("preservedSegment", ("headUuid", "tailUuid", "anchorUuid")),
        ("preservedMessages", ("anchorUuid", "uuids", "allUuids")),
        ("runningTurnSends", ("openerUuid", "uuids", "summarisedOnly")),
    ):
        for field in fields:
            value = metadata.get(name, {}).get(field, [])
            for ref in value if isinstance(value, list) else [value]:
                assert ref in written
    return metadata, mapping


async def test_fork_running_turn_references_resolve(fork_entries):
    rows = _transcript()
    original = rows[2]["compactMetadata"]
    # Equal to a real message ID, but not a declared reference field.
    original["extra"] = {"requestUuid": rows[0]["uuid"]}
    copied = await fork_entries(rows)
    metadata, mapping = _metadata_and_mapping(copied)
    assert metadata["runningTurnSends"] == {
        "openerUuid": mapping[rows[0]["uuid"]],
        "uuids": [mapping[rows[0]["uuid"]]],
        "summarisedOnly": [mapping[rows[1]["uuid"]]],
    }
    assert metadata["extra"] == original["extra"]
    assert copied[0]["message"] == rows[0]["message"]


@pytest.mark.parametrize("excluded_kind", ["cutoff", "sidechain", "progress"])
async def test_fork_trims_only_excluded_list_references(fork_entries, excluded_kind):
    rows = _transcript()
    excluded = rows[-1]["uuid"]
    metadata = rows[2]["compactMetadata"]
    for name, field in (
        ("preservedMessages", "uuids"),
        ("preservedMessages", "allUuids"),
        ("runningTurnSends", "uuids"),
        ("runningTurnSends", "summarisedOnly"),
    ):
        metadata[name][field].append(excluded)
    cutoff = rows[3]["uuid"] if excluded_kind == "cutoff" else None
    if excluded_kind == "sidechain":
        rows[-1]["isSidechain"] = True
    if excluded_kind == "progress":
        rows[-1]["type"] = "progress"
        rows[2]["logicalParentUuid"] = excluded
    copied = await fork_entries(rows, cutoff)
    actual, mapping = _metadata_and_mapping(copied)
    assert excluded not in mapping
    for name, field in (
        ("preservedMessages", "uuids"),
        ("preservedMessages", "allUuids"),
        ("runningTurnSends", "uuids"),
        ("runningTurnSends", "summarisedOnly"),
    ):
        assert actual[name][field] == [
            mapping[ref] for ref in metadata[name][field][:-1]
        ]


@pytest.mark.parametrize(
    ("name", "field"),
    [
        ("preservedSegment", "headUuid"),
        ("preservedSegment", "tailUuid"),
        ("preservedSegment", "anchorUuid"),
        ("preservedMessages", "anchorUuid"),
        ("runningTurnSends", "openerUuid"),
    ],
)
async def test_partial_fork_drops_block_with_excluded_endpoint(
    fork_entries, name, field
):
    rows = _transcript()
    rows[2]["compactMetadata"][name][field] = rows[-1]["uuid"]
    copied = await fork_entries(rows, rows[3]["uuid"])
    actual, mapping = _metadata_and_mapping(copied)
    assert name not in actual
    assert rows[-1]["uuid"] not in mapping
    assert actual["trigger"] == "auto"


async def test_fork_preserves_historical_and_unknown_ids(fork_entries):
    rows = _transcript()
    missing = str(uuid.uuid4())
    metadata = rows[2]["compactMetadata"]
    metadata["runningTurnSends"] = {
        "openerUuid": missing,
        "uuids": [missing, rows[0]["uuid"]],
        "summarisedOnly": [missing, rows[1]["uuid"]],
        "extra": {"uuid": rows[0]["uuid"]},
    }
    metadata["preservedMessages"]["allUuids"].append(missing)
    # A non-boundary record can carry arbitrary similarly named data.
    rows[0]["compactMetadata"] = copy.deepcopy(metadata)
    copied = await fork_entries(rows)
    mapping = {row["forkedFrom"]["messageUuid"]: row["uuid"] for row in copied}
    actual = copied[2]["compactMetadata"]
    assert actual["runningTurnSends"] == {
        "openerUuid": missing,
        "uuids": [missing, mapping[rows[0]["uuid"]]],
        "summarisedOnly": [missing, mapping[rows[1]["uuid"]]],
        "extra": {"uuid": rows[0]["uuid"]},
    }
    assert actual["preservedMessages"]["allUuids"] == [
        mapping[rows[0]["uuid"]],
        mapping[rows[1]["uuid"]],
        missing,
    ]
    assert copied[0]["compactMetadata"] == metadata


@pytest.mark.parametrize("running", [{}, {"uuids": []}, {"summarisedOnly": []}])
async def test_fork_keeps_optional_running_turn_fields_absent(fork_entries, running):
    rows = _transcript()
    rows[2]["compactMetadata"]["runningTurnSends"] = running
    copied = await fork_entries(rows)
    metadata, _ = _metadata_and_mapping(copied)
    assert metadata["runningTurnSends"] == running


async def test_partial_fork_can_trim_entire_preserved_list(fork_entries):
    rows = _transcript()
    excluded = rows[-1]["uuid"]
    metadata = rows[2]["compactMetadata"]
    metadata["preservedSegment"].update(headUuid=excluded, tailUuid=excluded)
    metadata["preservedMessages"].update(uuids=[excluded], allUuids=[excluded])
    metadata["runningTurnSends"]["summarisedOnly"] = [excluded]
    copied = await fork_entries(rows, rows[3]["uuid"])
    actual, mapping = _metadata_and_mapping(copied)
    assert "preservedSegment" not in actual
    assert actual["preservedMessages"] == {
        "anchorUuid": mapping[rows[3]["uuid"]],
        "uuids": [],
        "allUuids": [],
    }
    assert actual["runningTurnSends"]["summarisedOnly"] == []
