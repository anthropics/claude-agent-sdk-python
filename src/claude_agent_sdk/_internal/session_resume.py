"""Materialize a :class:`SessionStore`-backed resume into a temp ``CLAUDE_CONFIG_DIR``.

When ``options.resume`` (or ``options.continue_conversation``) is paired with
``options.session_store``, the session JSONL almost certainly does not exist on
local disk — it lives in the external store. The CLI subprocess only knows how
to resume from a local file. This module bridges the gap: it loads the session
from the store, writes it to a temporary directory laid out exactly like
``~/.claude/``, and returns the path so the caller can point the subprocess at
it via ``CLAUDE_CONFIG_DIR``.

Mirrors the behavior of the TypeScript SDK.
"""

from __future__ import annotations

import errno
import getpass
import json
import logging
import ntpath
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import anyio

from ..types import ClaudeAgentOptions, SessionKey, SessionStore, SessionStoreFlushMode
from .session_store_validation import _store_implements
from .sessions import (
    _agent_metadata_sidecar_path,
    _get_projects_dir,
    _split_agent_metadata,
    _validate_uuid,
    project_key_for_directory,
)
from .transcript_mirror_batcher import (
    MAX_PENDING_BYTES,
    MAX_PENDING_ENTRIES,
    TranscriptMirrorBatcher,
)

logger = logging.getLogger(__name__)

# Default macOS Keychain service name for OAuth credentials when
# CLAUDE_CONFIG_DIR is unset (production OAUTH_FILE_SUFFIX is empty).
_KEYCHAIN_SERVICE_NAME = "Claude Code-credentials"


@dataclass
class MaterializedResume:
    """Result of :func:`materialize_resume_session`.

    Attributes:
        config_dir: Temporary directory laid out like ``~/.claude/`` —
            point the subprocess at it via ``CLAUDE_CONFIG_DIR``.
        resume_session_id: Session ID to pass as ``--resume``. When the
            input was ``continue_conversation``, this is the most-recent
            session resolved via :meth:`SessionStore.list_sessions`.
        cleanup: Coroutine that removes ``config_dir`` (best-effort).
            Call it after the subprocess exits.
    """

    config_dir: Path
    resume_session_id: str
    cleanup: Callable[[], Awaitable[None]]


def apply_materialized_options(
    options: ClaudeAgentOptions, materialized: MaterializedResume
) -> ClaudeAgentOptions:
    """Return a copy of ``options`` repointed at a materialized temp config dir.

    Sets ``CLAUDE_CONFIG_DIR`` in ``env``, ``resume`` to the materialized
    session id, and clears ``continue_conversation`` (already resolved to a
    concrete session id during materialization).
    """
    return replace(
        options,
        env={
            **options.env,
            "CLAUDE_CONFIG_DIR": str(materialized.config_dir),
        },
        resume=materialized.resume_session_id,
        continue_conversation=False,
    )


def build_mirror_batcher(
    store: SessionStore,
    materialized: MaterializedResume | None,
    env: dict[str, str] | None,
    on_error: Callable[[SessionKey | None, str], Awaitable[None]],
    flush_mode: SessionStoreFlushMode = "batched",
) -> TranscriptMirrorBatcher:
    """Construct the :class:`TranscriptMirrorBatcher` for a session.

    Resolves ``projects_dir`` to the materialized temp dir when present
    (so file_path → key resolution matches what the subprocess writes),
    otherwise to the standard projects directory under the effective
    ``CLAUDE_CONFIG_DIR``.

    ``flush_mode="eager"`` zeroes the batcher's pending thresholds so every
    enqueued frame schedules a background flush; ``"batched"`` keeps the
    defaults (flush on ``result`` or 500-entry / 1 MiB overflow).
    """
    projects_dir = (
        str(materialized.config_dir / "projects")
        if materialized is not None
        else str(_get_projects_dir(env))
    )
    eager = flush_mode == "eager"
    return TranscriptMirrorBatcher(
        store=store,
        projects_dir=projects_dir,
        on_error=on_error,
        max_pending_entries=0 if eager else MAX_PENDING_ENTRIES,
        max_pending_bytes=0 if eager else MAX_PENDING_BYTES,
    )


async def materialize_resume_session(
    options: ClaudeAgentOptions,
) -> MaterializedResume | None:
    """Load a session from ``options.session_store`` and write it to a temp dir.

    Returns ``None`` when no materialization is needed (no store, no
    resume/continue, store has no entries, or the resolved session ID is not a
    valid UUID) — caller falls through to the normal (no-store) resume/spawn
    path. For ``continue_conversation`` this means a fresh session; for an
    explicit ``resume`` value the CLI receives it unchanged.

    Raises ``RuntimeError`` if a store call fails or times out.
    """
    store = options.session_store
    if store is None:
        return None
    if options.resume is None and not options.continue_conversation:
        return None

    timeout_s = options.load_timeout_ms / 1000
    project_key = project_key_for_directory(options.cwd)

    # Resolve the session ID — explicit resume wins; otherwise pick the
    # most-recently-modified non-sidechain session from the store. Empty
    # list_sessions() → fresh session (matches CLI --continue with no history).
    if options.resume is not None:
        # session_id is used as a path component below; reject anything that
        # isn't a UUID to prevent traversal and match every other resume path.
        if _validate_uuid(options.resume) is None:
            return None
        resolved = await _load_candidate(store, project_key, options.resume, timeout_s)
    else:
        resolved = await _resolve_continue_candidate(store, project_key, timeout_s)
    if resolved is None:
        return None
    session_id, entries = resolved

    tmp_base = Path(tempfile.mkdtemp(prefix="claude-resume-"))
    try:
        project_dir = tmp_base / "projects" / project_key
        project_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(project_dir / f"{session_id}.jsonl", entries)

        # The subprocess will run with CLAUDE_CONFIG_DIR=tmp_base. Copy auth
        # config from the caller's effective config locations so it can
        # authenticate. Missing files are fine (API-key auth, etc.).
        _copy_auth_files(tmp_base, options.env)

        # Materialize subagent transcripts if the store can enumerate them.
        if _store_implements(store, "list_subkeys"):
            await _materialize_subkeys(
                store, tmp_base, project_dir, project_key, session_id, timeout_s
            )
    except BaseException:
        # Any failure after mkdtemp leaves tmp_base (which may already
        # contain a .credentials.json copy) on disk with no path for the
        # caller to clean it up. Remove it before rethrowing. BaseException
        # so the backend's cancellation exception (a BaseException on both
        # asyncio and trio) also triggers cleanup — callers can't compensate
        # because the assignment raises before completing.
        await _rmtree_with_retry(tmp_base)
        raise

    async def cleanup() -> None:
        await _rmtree_with_retry(tmp_base)

    return MaterializedResume(
        config_dir=tmp_base,
        resume_session_id=session_id,
        cleanup=cleanup,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# OSError errnos that indicate a transiently-held handle (Windows AV/indexer
# scanning a freshly-written file) rather than a permanent failure.
_RETRYABLE_RMTREE_ERRNOS = frozenset(
    {
        errno.EBUSY,
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOTEMPTY,
        errno.EPERM,
        errno.EACCES,
    }
)


async def _rmtree_with_retry(
    path: Path, *, retries: int = 4, delay: float = 0.1
) -> None:
    """Best-effort ``shutil.rmtree`` with retries on transient lock errors.

    On Windows, AV/indexer can briefly hold a handle on freshly-written
    files (notably ``.credentials.json``), causing rmtree to fail with
    EBUSY/EPERM. Retry a few times with a short backoff; after exhausting
    retries, fall back to ``ignore_errors=True`` (matches the previous
    behavior, but gives the handle a chance to release first so the access
    token doesn't leak in temp). Never raises.
    """
    if not path.exists():
        return
    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except OSError as e:
            if e.errno not in _RETRYABLE_RMTREE_ERRNOS and not isinstance(
                e, PermissionError
            ):
                break
        try:
            await anyio.sleep(delay)
        except anyio.get_cancelled_exc_class():
            # Best-effort final sweep before propagating cancellation so a
            # cancelled connect() doesn't leak the temp dir.
            shutil.rmtree(path, ignore_errors=True)
            raise
    shutil.rmtree(path, ignore_errors=True)


async def _load_candidate(
    store: SessionStore, project_key: str, session_id: str, timeout_s: float
) -> tuple[str, list[Any]] | None:
    """Load entries for ``session_id``; return ``None`` if empty/missing."""
    entries = await _with_timeout(
        store.load({"project_key": project_key, "session_id": session_id}),
        timeout_s,
        f"SessionStore.load() for session {session_id}",
    )
    if not entries:
        return None
    return session_id, entries


async def _resolve_continue_candidate(
    store: SessionStore, project_key: str, timeout_s: float
) -> tuple[str, list[Any]] | None:
    """Pick the most-recently-modified non-sidechain session.

    Sidechain transcripts are mirrored as ordinary top-level keys and often
    have the highest mtime (their append lands after the main session's in
    the same flush). Walk newest→oldest, loading each candidate (the load is
    needed anyway) and skipping sidechains so ``--continue`` resumes the
    user's conversation, not a subagent's. Matches the CLI's own
    ``--continue`` filter and ``list_sessions_from_store()``.
    """
    sessions = await _with_timeout(
        store.list_sessions(project_key),
        timeout_s,
        "SessionStore.list_sessions()",
    )
    if not sessions:
        return None
    for cand in sorted(sessions, key=lambda s: s["mtime"], reverse=True):
        sid = cand["session_id"]
        if _validate_uuid(sid) is None:
            continue
        loaded = await _load_candidate(store, project_key, sid, timeout_s)
        if loaded is None:
            continue
        first = loaded[1][0]
        if isinstance(first, dict) and first.get("isSidechain") is True:
            continue
        return loaded
    return None


async def _with_timeout(coro: Awaitable[Any], timeout_s: float, what: str) -> Any:
    """Await ``coro`` with a timeout, re-raising as ``RuntimeError`` with context."""
    try:
        with anyio.fail_after(timeout_s):
            return await coro
    except TimeoutError as e:
        raise RuntimeError(
            f"{what} timed out after {int(timeout_s * 1000)}ms during resume "
            f"materialization"
        ) from e
    except Exception as e:  # noqa: BLE001 - surface adapter failures with context
        raise RuntimeError(f"{what} failed during resume materialization: {e}") from e


def _write_jsonl(path: Path, entries: list[Any]) -> None:
    """Stream-write ``entries`` as one JSON line each to ``path`` (mode 0o600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")))
            f.write("\n")
    with suppress(OSError):
        path.chmod(0o600)


def _copy_auth_files(tmp_base: Path, opt_env: dict[str, str]) -> None:
    """Seed ``tmp_base`` with the caller's auth and user config: ``.credentials.json``
    (refreshToken redacted), ``.claude.json``, and user ``settings.json`` /
    ``cowork_settings.json`` (plugin declarations stripped).

    Source resolution mirrors the CLI:
    - ``.credentials.json``, ``settings.json`` and ``cowork_settings.json`` live
      under the config dir (default ``~/.claude/``)
    - ``.claude.json`` lives at ``$CLAUDE_CONFIG_DIR/.claude.json`` when set,
      else ``~/.claude.json`` (NOT ``~/.claude/.claude.json``)
    """
    caller_config_dir = opt_env.get("CLAUDE_CONFIG_DIR") or os.environ.get(
        "CLAUDE_CONFIG_DIR"
    )
    source_config_dir = (
        Path(caller_config_dir) if caller_config_dir else Path.home() / ".claude"
    )

    creds_bytes = _read_if_present(source_config_dir / ".credentials.json")
    creds_json = creds_bytes.decode("utf-8") if creds_bytes is not None else None

    # macOS default setup keeps OAuth tokens in the Keychain, not a file.
    # Redirecting CLAUDE_CONFIG_DIR changes the Keychain service-name suffix,
    # so the subprocess's lookup misses and falls back to plainTextStorage at
    # ${tmp_base}/.credentials.json. Populate that file from the parent's
    # Keychain so the resumed subprocess can auth. Skipped when env-based
    # auth or a custom config dir is already in play.
    if (
        caller_config_dir is None
        and not (
            opt_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        )
        and not (
            opt_env.get("CLAUDE_CODE_OAUTH_TOKEN")
            or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        )
    ):
        keychain = _read_keychain_credentials()
        if keychain is not None:
            creds_json = keychain

    _write_redacted_credentials(creds_json, tmp_base / ".credentials.json")

    claude_json_src = (
        Path(caller_config_dir) / ".claude.json"
        if caller_config_dir
        else Path.home() / ".claude.json"
    )
    _copy_if_present(claude_json_src, tmp_base / ".claude.json")

    # User settings carry ``apiKeyHelper`` (a fourth auth mechanism alongside
    # .credentials.json / Keychain / env) plus env/hooks/permissions. Without
    # it the resumed subprocess sees no user settings at all, and an
    # apiKeyHelper-only host fails with "Not logged in". cowork_settings.json
    # is the alternate filename the CLI reads in cowork-plugins mode. Both pass
    # through _strip_settings_for_resume so plugin declarations don't
    # reconcile against the empty tmp_base plugin cache.
    for name in ("settings.json", "cowork_settings.json"):
        _copy_if_present(
            source_config_dir / name, tmp_base / name, _strip_settings_for_resume
        )


# User-settings keys that only misbehave under the redirected CLAUDE_CONFIG_DIR:
# plugin declarations reconcile against the always-empty tmp_base/plugins cache
# and would network-install each declared marketplace on every resume.
_RESUME_SETTINGS_STRIPPED_KEYS = ("enabledPlugins", "extraKnownMarketplaces")


def _strip_settings_for_resume(content: bytes) -> bytes:
    """Drop settings keys that misbehave under a redirected config dir.

    Removes :data:`_RESUME_SETTINGS_STRIPPED_KEYS` and ``env.CLAUDE_CONFIG_DIR``
    (which would point the subprocess's config reads away from ``tmp_base``).
    Content that doesn't parse as a JSON object is returned untouched so the
    subprocess sees exactly what the CLI would have read.
    """
    try:
        # utf-8-sig mirrors the CLI's settings reader: PowerShell writes
        # settings.json with a UTF-8 BOM, which a plain json.loads rejects.
        parsed = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return content
    if not isinstance(parsed, dict):
        return content
    stripped = False
    for key in _RESUME_SETTINGS_STRIPPED_KEYS:
        if key in parsed:
            del parsed[key]
            stripped = True
    env_block = parsed.get("env")
    if isinstance(env_block, dict) and "CLAUDE_CONFIG_DIR" in env_block:
        del env_block["CLAUDE_CONFIG_DIR"]
        stripped = True
    if not stripped:
        return content
    try:
        # allow_nan=False: a spec-valid overflow like 1e999 parses to inf, and
        # re-serializing it as the bare token ``Infinity`` would produce JSON
        # the CLI rejects. Fall back to the original bytes instead.
        return json.dumps(parsed, separators=(",", ":"), allow_nan=False).encode()
    except ValueError:
        return content


def _write_redacted_credentials(creds_json: str | None, dst: Path) -> None:
    """Write ``creds_json`` with ``claudeAiOauth.refreshToken`` removed.

    The resumed subprocess runs under a redirected ``CLAUDE_CONFIG_DIR``; if it
    refreshed, the single-use refresh token would be consumed server-side and
    the new tokens written to a location the parent never reads back — leaving
    the parent's stored creds revoked. With no ``refreshToken``, the
    subprocess's refresh check short-circuits.
    """
    if creds_json is None:
        return
    out = creds_json
    try:
        data = json.loads(creds_json)
        oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
        if isinstance(oauth, dict) and "refreshToken" in oauth:
            del oauth["refreshToken"]
            out = json.dumps(data)
    except (json.JSONDecodeError, ValueError):
        # Unparseable — write through; subprocess will fail to parse it too.
        pass
    dst.write_text(out, encoding="utf-8")
    with suppress(OSError):
        dst.chmod(0o600)


def _read_if_present(src: Path) -> bytes | None:
    """Read a regular file, or return ``None``.

    A missing source is skipped silently. Any other reason it can't be read
    (EACCES, a directory or FIFO where a file was expected, ...) is logged and
    skipped: these files are best-effort enrichment of the temp config dir, so
    an unreadable one must not abort -- or, for a FIFO, hang -- the resume.
    """
    try:
        if not stat.S_ISREG(src.stat().st_mode):
            raise OSError(f"{src} is not a regular file")
        return src.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("[SessionStore] resume: skipping %s (%s)", src, e)
        return None


def _copy_if_present(
    src: Path, dst: Path, transform: Callable[[bytes], bytes] | None = None
) -> None:
    """Copy ``src`` to ``dst`` (mode 0o600) if it exists, through an optional
    ``transform``. See :func:`_read_if_present` for the skip policy."""
    content = _read_if_present(src)
    if content is None:
        return
    try:
        dst.write_bytes(transform(content) if transform else content)
        with suppress(OSError):
            dst.chmod(0o600)
    except OSError as e:
        # Don't leave a truncated dst behind for the subprocess to misparse.
        with suppress(OSError):
            dst.unlink(missing_ok=True)
        logger.warning("[SessionStore] resume: skipping %s (%s)", src, e)


def _read_keychain_credentials() -> str | None:
    """Read OAuth credentials JSON from the macOS Keychain (default service name).

    Best-effort — returns ``None`` on any error or non-macOS platforms.
    """
    # platform.system() (not sys.platform) so mypy doesn't narrow the rest
    # of the function to unreachable on the typecheck host.
    if platform.system() != "Darwin":
        return None
    try:
        user = os.environ.get("USER") or getpass.getuser()
    except Exception:
        user = "claude-code-user"
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                user,
                "-w",
                "-s",
                _KEYCHAIN_SERVICE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


async def _materialize_subkeys(
    store: SessionStore,
    tmp_base: Path,
    project_dir: Path,
    project_key: str,
    session_id: str,
    timeout_s: float,
) -> None:
    """Load and write all subagent transcripts/metadata under ``session_id``."""
    session_dir = project_dir / session_id
    subkeys = await _with_timeout(
        store.list_subkeys({"project_key": project_key, "session_id": session_id}),
        timeout_s,
        f"SessionStore.list_subkeys() for session {session_id}",
    )
    for subpath in subkeys:
        # Subpaths come from an external store and are used as filesystem path
        # components below. Reject anything that would escape the session
        # directory. Empty string is rejected explicitly: '' + '.jsonl' →
        # '.jsonl', a hidden dotfile that passes a naive prefix check.
        if not _is_safe_subpath(subpath, session_dir):
            logger.warning(
                "[SessionStore] skipping unsafe subpath from list_subkeys: %r", subpath
            )
            continue

        sub_key: SessionKey = {
            "project_key": project_key,
            "session_id": session_id,
            "subpath": subpath,
        }
        sub_entries = await _with_timeout(
            store.load(sub_key),
            timeout_s,
            f"SessionStore.load() for session {session_id} subpath {subpath}",
        )
        if not sub_entries:
            continue

        # agent_metadata entries describe the .meta.json sidecar (last one
        # wins); everything else is a transcript line.
        metadata, transcript = _split_agent_metadata(sub_entries)

        sub_file = (session_dir / subpath).with_name(
            (session_dir / subpath).name + ".jsonl"
        )
        if transcript:
            _write_jsonl(sub_file, transcript)

        if metadata is not None:
            # Strip the synthetic ``type`` field.
            meta_content = {k: v for k, v in metadata.items() if k != "type"}
            meta_file = _agent_metadata_sidecar_path(sub_file)
            meta_file.parent.mkdir(parents=True, exist_ok=True)
            meta_file.write_text(json.dumps(meta_content), encoding="utf-8")
            with suppress(OSError):
                meta_file.chmod(0o600)


def _is_safe_subpath(subpath: str, session_dir: Path) -> bool:
    """Reject subpaths that are empty, absolute, contain ``..``, or escape
    ``session_dir`` after resolution."""
    if not subpath:
        return False
    # PurePosixPath/PureWindowsPath both checked — subpaths are store keys
    # that may use either separator regardless of host OS.
    if Path(subpath).is_absolute() or subpath.startswith(("/", "\\")):
        return False
    # Drive-prefixed (``C:foo``) and UNC subpaths are never legitimate store
    # keys. ``ntpath.splitdrive`` is used regardless of host OS so a Windows
    # consumer is protected even if the store was populated elsewhere; on
    # POSIX this also rejects ``C:foo``, which is acceptable since the only
    # subpaths we ever emit are ``subagents/...``.
    if ntpath.splitdrive(subpath)[0]:
        return False
    if any(p in (".", "..") for p in re.split(r"[\\/]", subpath)):
        return False
    if "\x00" in subpath:
        return False
    # Resolve the .jsonl target — using the same expression as the writer in
    # _materialize_subkeys so the validated path can't drift from the written
    # one — and confirm it stays under session_dir. Both ``.resolve()`` calls
    # can raise (e.g. ValueError on embedded NUL, OSError on broken symlink
    # chains); treat any resolution failure as unsafe so the subpath is
    # skipped with a warning rather than aborting the whole resume.
    target = session_dir / subpath
    try:
        sub_file = target.with_name(target.name + ".jsonl").resolve()
        sub_file.relative_to(session_dir.resolve())
    except (ValueError, OSError):
        return False
    return True
