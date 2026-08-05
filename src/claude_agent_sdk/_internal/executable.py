r"""Safe executable resolution and invocation for helper programs.

**Safe executable resolution.** This SDK never launches a helper program by
bare name. Every program it spawns is either an absolute path it constructed
itself, or a bare name resolved by the SDK's own ``find_executable`` -- which
searches only the fully-absolute entries of ``PATH``, never the current
working directory (neither implicitly, as Windows ``CreateProcess``/
``shutil.which``/libuv do, nor via ``.``/empty/relative ``PATH`` entries),
and on Windows returns only native executables (``.exe``/``.com``). The
absolute path it returns is what is handed to the OS. This holds on every
platform, so a file planted in a directory the user merely *works in* (a
cloned repository, an extracted archive) is never selected as a helper
binary.

Guarantees (the tests, the docs and the sibling SDKs refer to them by name):

- G1 -- absolute argv[0]. The program path handed to the OS process-creation
  API is always absolute. Bare names never reach ``subprocess`` / ``anyio``.
- G2 -- the current directory is never a search location. Not implicitly
  (Windows), not via ``.``, not via an empty ``PATH`` entry (which POSIX
  ``execvp`` treats as the current directory), not via a relative entry
  (``bin``, ``..\tools``), and on Windows not via a drive-relative
  (``C:bin``) or rooted-but-driveless (``\bin``) entry either. Only fully
  absolute entries are searched: POSIX ``/...``; Windows ``X:\...`` /
  ``X:/...`` or UNC ``\\server\share\...``. One pair of surrounding double
  quotes on a Windows entry is stripped first (``"C:\Program Files\Git\cmd"``
  is legal in ``PATH``). ``~`` and ``%VAR%`` / ``$VAR`` are not expanded --
  the OS does not expand them at spawn time either -- so such entries are
  skipped as relative. An unset or empty ``PATH`` finds nothing: there is no
  ``os.defpath`` fallback.
- G3 -- Windows: native images only. For a bare name the candidates are the
  name with each *allowed* extension appended, by default ``.exe`` and
  ``.com`` -- never ``.bat`` / ``.cmd`` (``CreateProcess`` runs those through
  ``cmd.exe /c``, which re-parses the arguments: the BatBadBut /
  CVE-2024-27980 class) and never an extensionless file (WinError 193). A
  name that already ends in an allowed extension is tried as-is only; a name
  with any other extension has no candidates at all. The allow-list is a
  parameter so a caller can *detect* -- not run -- a ``.cmd`` shim in order
  to explain why it is refused. On POSIX the name is tried as-is only.
- G4 -- explicit paths are the caller's decision. If the name contains a
  path separator (``/``; on Windows also ``\`` or a drive colon) nothing is
  searched: the absolute, normalised form is returned iff it is an existing
  regular file (and executable, on POSIX), else "not found". ``./tool``
  therefore resolves against the current directory -- the caller asked for
  exactly that.
- G5 -- one implementation, enforced. ``shutil.which`` and
  ``distutils.spawn.find_executable`` are banned by ruff (the
  ``flake8-tidy-imports`` ``banned-api`` table in ``pyproject.toml``), and
  ``tests/test_executable_invariant.py`` fails if any process-spawning call
  under ``src/`` passes a program name literal that is not an absolute path.
- D1 -- defense in depth, Windows only. The transport module sets
  ``NoDefaultCurrentDirectoryInExePath`` in this process's environment at
  import, so ``CreateProcess`` (and ``shutil.which`` on Python >= 3.12) stop
  searching the current directory for anything downstream of the SDK that
  still does an OS-level bare-name lookup. It is not the fix -- older Pythons
  ignore it -- G1-G4 are.

A match is a regular file (symlinks followed, so a directory named like the
tool is skipped) that is executable (``os.access(X_OK)``) on POSIX; on
Windows existence plus an allowed extension is what makes a file runnable.
The result is ``normpath(join(entry, candidate))`` -- deliberately not
``realpath``, which keeps Homebrew / Scoop symlink-farm spellings intact --
and nothing is cached: ``PATH`` and the current directory change, and the
walk is cheap.

Why not ``shutil.which(name, path=sanitized)``: on Windows CPython puts the
current directory at the front of the search even when ``path=`` is given
(unconditionally before 3.12; unless ``NoDefaultCurrentDirectoryInExePath``
is set from 3.12 on) and appends every ``PATHEXT`` extension, ``.bat`` and
``.cmd`` included. So the walk is written out here, with the platform rules
as pure ``ntpath`` / ``posixpath`` string functions that the tests exercise
on every host.

Mirrors claude-code's ``safeExecutableResolver``; keep in sync with the other
Anthropic SDKs (claude-agent-sdk-python, anthropic-sdk-python,
anthropic-sdk-typescript, anthropic-sdk-go).
"""

from __future__ import annotations

import errno
import ntpath
import os
import posixpath
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

WINDOWS_NATIVE_EXTENSIONS: Final = (".exe", ".com")

# G2 on Windows: drive-absolute ("C:\", "c:/") or UNC ("\\server\share").
_WINDOWS_ABSOLUTE_ENTRY = re.compile(r"[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/]+[^\\/]+")


class ExecutableNotFoundError(FileNotFoundError):
    """No executable matched ``name`` (see :func:`require_executable`).

    A :class:`FileNotFoundError`, so callers that already treat a missing
    helper program as an ``OSError`` keep working unchanged.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            errno.ENOENT,
            "executable not found (only absolute PATH entries are searched,"
            " never the current directory)",
            name,
        )
        self.name = name


def _is_windows() -> bool:
    # os.name rather than sys.platform so mypy does not narrow either branch
    # away on the host it type-checks on.
    return os.name == "nt"


def _unquote_path_entry(entry: str, *, windows: bool) -> str:
    r"""Strip one pair of surrounding double quotes from a Windows PATH entry.

    ``"C:\Program Files\X"`` is a legal Windows ``PATH`` entry; the quotes
    are not part of the directory name. POSIX attaches no meaning to them.
    """
    if windows and len(entry) >= 2 and entry[0] == entry[-1] == '"':
        return entry[1:-1]
    return entry


def _is_searchable_path_entry(entry: str, *, windows: bool) -> bool:
    r"""G2: whether a ``PATH`` entry is fully absolute, and therefore searched.

    Empty, ``.``, relative, ``~``- or variable-prefixed entries -- and on
    Windows drive-relative (``C:bin``) and rooted-but-driveless (``\bin``)
    ones -- all resolve against some current directory, so they are skipped.
    """
    entry = _unquote_path_entry(entry, windows=windows)
    if windows:
        return _WINDOWS_ABSOLUTE_ENTRY.match(entry) is not None
    return entry.startswith("/")


def _candidate_names(
    name: str, *, windows: bool, windows_extensions: Sequence[str]
) -> list[str]:
    """G3: the file names looked for in each searched directory.

    POSIX: the name as-is. Windows: the name as-is if it already ends in an
    allowed extension; nothing at all if it carries any other extension
    (``claude.cmd`` and ``tool.js`` are never candidates under the default
    allow-list); otherwise the name with each allowed extension appended, in
    order -- and never the bare extensionless name.
    """
    if not windows:
        return [name]
    lowered = name.lower()
    if any(lowered.endswith(ext.lower()) for ext in windows_extensions):
        return [name]
    if ntpath.splitext(name)[1]:
        return []
    return [name + ext for ext in windows_extensions]


def _is_explicit_path(name: str, *, windows: bool) -> bool:
    r"""G4: whether ``name`` spells out a location rather than a bare command.

    Any directory part counts, a bare drive (``C:claude``) included. On POSIX
    a backslash is an ordinary file-name character, so ``a\b`` is still a
    bare name there.
    """
    if windows:
        return bool(ntpath.dirname(name))
    return bool(posixpath.dirname(name))


def _iter_candidate_paths(
    name: str, path: str, *, windows: bool, windows_extensions: Sequence[str]
) -> Iterator[str]:
    """Absolute, normalised candidate paths for bare ``name``, in ``PATH`` order.

    G2 and G3 combined, as pure string logic: nothing here touches the
    filesystem, so the Windows walk is testable on any host.
    """
    names = _candidate_names(
        name, windows=windows, windows_extensions=windows_extensions
    )
    if not names:
        return
    for raw_entry in path.split(ntpath.pathsep if windows else posixpath.pathsep):
        entry = _unquote_path_entry(raw_entry, windows=windows)
        if not _is_searchable_path_entry(entry, windows=windows):
            continue
        for candidate in names:
            if windows:
                yield ntpath.normpath(ntpath.join(entry, candidate))
            else:
                yield posixpath.normpath(posixpath.join(entry, candidate))


def _is_executable_file(path: str, *, windows: bool) -> bool:
    """The match test for a candidate path.

    An existing regular file -- symlinks followed, so a directory named like
    the tool never matches -- that is executable on POSIX. On Windows there
    is no execute bit to consult: existence plus the allowed extension the
    candidate was built with is what makes it runnable.
    """
    try:
        if not Path(path).is_file():
            return False
    except OSError:
        # PATH may name places this process cannot even stat (another
        # user's directory, a dead network share). That is "not here".
        return False
    return windows or os.access(path, os.X_OK)


def find_executable(
    name: str,
    *,
    path: str | None = None,
    windows_extensions: Sequence[str] = WINDOWS_NATIVE_EXTENSIONS,
) -> str | None:
    """Resolve ``name`` to the absolute path of an executable, or ``None``.

    A bare name is looked up in the fully-absolute entries of ``path``
    (default: the ``PATH`` environment variable) only -- never in the current
    directory, whether implicitly or via ``.`` / empty / relative entries
    (G2) -- and on Windows only as a native ``.exe`` / ``.com`` image, or
    whatever ``windows_extensions`` allows (G3). A name that contains a path
    separator is not searched for: its absolute form is returned iff that
    file exists and is executable (G4). Whatever is returned is absolute and
    normalised, ready to be handed to the OS as ``argv[0]`` (G1). See the
    module docstring for the full contract.
    """
    windows = _is_windows()
    if name in ("", ".", ".."):
        return None
    if _is_explicit_path(name, windows=windows):
        candidate = ntpath.abspath(name) if windows else posixpath.abspath(name)
        return candidate if _is_executable_file(candidate, windows=windows) else None
    if path is None:
        path = os.environ.get("PATH", "")
    for candidate in _iter_candidate_paths(
        name, path, windows=windows, windows_extensions=windows_extensions
    ):
        if _is_executable_file(candidate, windows=windows):
            return candidate
    return None


def require_executable(
    name: str,
    *,
    path: str | None = None,
    windows_extensions: Sequence[str] = WINDOWS_NATIVE_EXTENSIONS,
) -> str:
    """:func:`find_executable`, raising :class:`ExecutableNotFoundError` on no match."""
    resolved = find_executable(name, path=path, windows_extensions=windows_extensions)
    if resolved is None:
        raise ExecutableNotFoundError(name)
    return resolved


def resolve_argv(
    argv: Sequence[str | os.PathLike[str]],
    *,
    path: str | None = None,
    windows_extensions: Sequence[str] = WINDOWS_NATIVE_EXTENSIONS,
) -> list[str]:
    """``argv`` with ``argv[0]`` replaced by its :func:`require_executable` result.

    The remaining arguments are passed through untouched (G1 concerns the
    program only).
    """
    if not argv:
        raise ValueError("argv must name a program to run")
    program, *args = (os.fspath(arg) for arg in argv)
    return [
        require_executable(program, path=path, windows_extensions=windows_extensions),
        *args,
    ]


def run(
    argv: Sequence[str | os.PathLike[str]], /, **kwargs: Any
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run(resolve_argv(argv), **kwargs)``.

    The one blessed way for SDK code to run a helper program synchronously:
    the program is resolved by :func:`require_executable` first, so the OS is
    only ever handed an absolute path (G1) and a missing program raises
    :class:`ExecutableNotFoundError` before anything is spawned. ``shell``
    and ``executable`` are refused because either would hand program lookup
    back to a shell or to the OS.
    """
    for unsupported in ("shell", "executable"):
        if kwargs.get(unsupported):
            raise TypeError(f"run() does not support the {unsupported!r} argument")
    return subprocess.run(resolve_argv(argv), **kwargs)
