"""Persist session-scoped plan files alongside transcript entries."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, cast

from ..types import SessionStoreEntry

PLAN_SNAPSHOT_TYPE = "sdk_plan_snapshot"
MAX_PLAN_BYTES = 2_000_000
_SLUG = re.compile(
    r"(?!(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])\Z)[A-Za-z0-9][A-Za-z0-9_-]{0,199}\Z"
)


def snapshot_plan_files(
    entries: list[SessionStoreEntry], config_dir: Path
) -> list[SessionStoreEntry]:
    """Capture only validated plan names referenced by this transcript batch."""
    slugs: set[str] = set()
    for entry in entries:
        slug = entry.get("slug")
        if isinstance(slug, str) and _SLUG.fullmatch(slug):
            slugs.add(slug)
    snapshots: list[SessionStoreEntry] = []
    plans_dir = config_dir / "plans"
    if plans_dir.is_symlink():
        return entries
    for slug in sorted(slugs):
        path = plans_dir / f"{slug}.md"
        if path.is_symlink():
            continue
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
        except FileNotFoundError:
            content = None
        except OSError:
            continue
        else:
            try:
                with os.fdopen(descriptor, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PLAN_BYTES:
                        continue
                    data = handle.read(MAX_PLAN_BYTES + 1)
            except OSError:
                continue
            if len(data) > MAX_PLAN_BYTES:
                continue
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
        snapshots.append(
            cast(
                SessionStoreEntry,
                {"type": PLAN_SNAPSHOT_TYPE, "slug": slug, "content": content},
            )
        )
    return [*entries, *snapshots]


def restore_plan_files(entries: list[Any], config_dir: Path) -> list[Any]:
    """Restore the latest snapshots without forwarding SDK metadata to the CLI."""
    transcript = []
    plans: dict[str, str | None] = {}
    current_slug: str | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            transcript.append(entry)
            continue
        slug = entry.get("slug")
        if isinstance(slug, str) and _SLUG.fullmatch(slug):
            current_slug = slug
        if entry.get("type") == PLAN_SNAPSHOT_TYPE:
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                continue
            if "content" not in entry:
                continue
            content = entry["content"]
        else:
            transcript.append(entry)
            slug = current_slug
            content = _embedded_plan_content(entry)
            if content is None:
                continue
        if slug is None:
            continue
        try:
            valid = content is None or (
                isinstance(content, str)
                and len(content.encode("utf-8")) <= MAX_PLAN_BYTES
            )
        except UnicodeEncodeError:
            valid = False
        if valid:
            plans[slug] = content
    for slug, content in plans.items():
        path = config_dir / "plans" / f"{slug}.md"
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content)
        path.chmod(0o600)
    return transcript


def _embedded_plan_content(entry: dict[str, Any]) -> Any:
    if entry.get("type") == "user":
        return entry.get("planContent")
    if entry.get("type") == "attachment":
        attachment = entry.get("attachment")
        if (
            isinstance(attachment, dict)
            and attachment.get("type") == "plan_file_reference"
        ):
            return attachment.get("planContent")
    if entry.get("type") == "system" and entry.get("subtype") == "file_snapshot":
        snapshots = entry.get("snapshotFiles")
        if isinstance(snapshots, list):
            for snapshot in reversed(snapshots):
                if isinstance(snapshot, dict) and snapshot.get("key") == "plan":
                    return snapshot.get("content")
    if entry.get("type") == "assistant":
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if isinstance(blocks, list):
            for block in reversed(blocks):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "ExitPlanMode"
                    and isinstance(block.get("input"), dict)
                ):
                    return block["input"].get("plan")
    return None
