"""Tests for the SDK's safe executable resolution (``_internal/executable.py``).

The vectors are shared with the other Anthropic SDKs and named after the
guarantees in that module's docstring: V1-V12 exercise the real filesystem on
whatever host runs the suite, W1-W3 exercise the Windows rules as pure string
logic (so they run on POSIX CI too), P1 is the POSIX-only backslash rule. The
per-callsite regression tests live next to their subjects (``_find_cli`` in
test_transport.py, the git worktree lookup in test_sessions.py, the Keychain
read in test_session_resume.py); the G5 enforcement scan is
test_executable_invariant.py.
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from claude_agent_sdk._internal import executable
from claude_agent_sdk._internal.executable import (
    WINDOWS_NATIVE_EXTENSIONS,
    ExecutableNotFoundError,
    find_executable,
    require_executable,
    resolve_argv,
    run,
)

WINDOWS = os.name == "nt"
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX execute bit / file names")

# What the bare name "tool" resolves to on this host: G3 appends .exe on
# Windows and never returns the extensionless file there.
TOOL = "tool.exe" if WINDOWS else "tool"


def _make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``bin/`` holds the real tool. ``plant/`` becomes the current directory
    and holds a planted one, plus ``rel/sub/tool`` beneath it. Every location
    carries both spellings (``tool`` and ``tool.exe``) so the same vectors run
    on every host."""
    for directory in ("bin", "plant", "plant/rel/sub"):
        for name in ("tool", "tool.exe"):
            _make_executable(tmp_path / directory / name)
    monkeypatch.chdir(tmp_path / "plant")
    return tmp_path


def _join(*entries: str) -> str:
    return os.pathsep.join(entries)


class TestSearchNeverUsesTheCurrentDirectory:
    """G2 on the host platform, against real files (V1-V5, V12)."""

    def test_v1_absolute_entry_is_searched(self, layout: Path) -> None:
        found = find_executable("tool", path=str(layout / "bin"))
        assert found == str(layout / "bin" / TOOL)

    def test_v2_empty_entry_is_not_the_current_directory(self, layout: Path) -> None:
        # POSIX execvp treats an empty PATH entry as "."; we skip it.
        found = find_executable("tool", path=_join("", str(layout / "bin")))
        assert found == str(layout / "bin" / TOOL)

    def test_v3_dot_entry_is_skipped(self, layout: Path) -> None:
        found = find_executable("tool", path=_join(".", str(layout / "bin")))
        assert found == str(layout / "bin" / TOOL)

    def test_v4_relative_entry_is_skipped(self, layout: Path) -> None:
        assert (Path("rel/sub") / TOOL).is_file()  # reachable from the cwd...
        found = find_executable(
            "tool", path=_join(str(Path("rel", "sub")), str(layout / "bin"))
        )
        assert found == str(layout / "bin" / TOOL)  # ...and still not used

    def test_v5_only_cwd_reachable_entries_find_nothing(self, layout: Path) -> None:
        assert Path(TOOL).is_file()  # the plant is right here in the cwd
        path = _join(".", "", "rel/sub", str(Path("rel", "sub")), "~", "plant")
        assert find_executable("tool", path=path) is None

    def test_v12_unset_or_empty_path_finds_nothing(
        self, layout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No os.defpath fallback and, above all, no fallback to the cwd.
        assert find_executable("tool", path="") is None
        monkeypatch.delenv("PATH", raising=False)
        assert find_executable("tool") is None

    def test_path_defaults_to_the_environment(
        self, layout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", _join(".", str(layout / "bin")))
        assert find_executable("tool") == str(layout / "bin" / TOOL)

    def test_first_searchable_entry_wins(self, layout: Path) -> None:
        _make_executable(layout / "other" / TOOL)
        found = find_executable(
            "tool", path=_join(str(layout / "other"), str(layout / "bin"))
        )
        assert found == str(layout / "other" / TOOL)


class TestMatchTest:
    """What counts as a hit (V6, V7)."""

    @posix_only
    def test_v6_non_executable_file_is_not_a_match(self, layout: Path) -> None:
        (layout / "bin" / "tool").chmod(0o644)
        assert find_executable("tool", path=str(layout / "bin")) is None

    def test_v7_directory_named_like_the_tool_is_skipped(self, layout: Path) -> None:
        (layout / "dirs" / TOOL).mkdir(parents=True)
        found = find_executable(
            "tool", path=_join(str(layout / "dirs"), str(layout / "bin"))
        )
        assert found == str(layout / "bin" / TOOL)

    @posix_only
    def test_symlinked_executable_keeps_its_path_spelling(self, layout: Path) -> None:
        # Homebrew / Scoop style symlink farms: follow the link for the match
        # test, but return the PATH spelling rather than the realpath.
        farm = layout / "farm"
        farm.mkdir()
        (farm / "tool").symlink_to(layout / "bin" / "tool")
        assert find_executable("tool", path=str(farm)) == str(farm / "tool")

    def test_unreadable_entry_is_not_an_error(
        self, layout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_is_file = Path.is_file

        def flaky_is_file(self: Path) -> bool:
            if self.parent.name == "locked":
                raise PermissionError("simulated EACCES")
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", flaky_is_file)
        found = find_executable(
            "tool", path=_join(str(layout / "locked"), str(layout / "bin"))
        )
        assert found == str(layout / "bin" / TOOL)


class TestExplicitPaths:
    """G4: a name with a directory part is the caller's decision (V8, V9, V11)."""

    def test_v8_dot_slash_resolves_against_the_current_directory(
        self, layout: Path
    ) -> None:
        found = find_executable("./tool", path=str(layout / "bin"))
        assert found == str(layout / "plant" / "tool")

    def test_v9_absolute_path_is_returned_as_is(self, layout: Path) -> None:
        target = str(layout / "bin" / TOOL)
        assert find_executable(target, path="") == target
        assert find_executable(str(layout / "bin" / "nope"), path="") is None

    def test_explicit_path_to_a_directory_is_not_a_match(self, layout: Path) -> None:
        assert find_executable(str(layout / "bin"), path="") is None

    @posix_only
    def test_explicit_non_executable_file_is_not_a_match(self, layout: Path) -> None:
        (layout / "plant" / "tool").chmod(0o644)
        assert find_executable("./tool", path="") is None

    def test_v11_degenerate_names_find_nothing(self, layout: Path) -> None:
        for name in ("", ".", ".."):
            assert find_executable(name, path=str(layout / "bin")) is None


class TestResultShape:
    """G1: whatever comes back can go straight to the OS (V10)."""

    def test_v10_result_is_absolute_and_normalised(self, layout: Path) -> None:
        messy = str(layout / "bin") + os.sep + "." + os.sep
        found = find_executable("tool", path=messy)
        assert found is not None
        assert Path(found).is_absolute()
        assert found == os.path.normpath(found)
        assert found == str(layout / "bin" / TOOL)


class TestWindowsRules:
    """W1-W3: the Windows walk as pure string logic, runnable on any host."""

    @pytest.mark.parametrize(
        "entry",
        [
            "C:\\bin",
            "c:/bin",
            "C:\\",
            "\\\\srv\\share\\bin",
            "//srv/share",
            '"C:\\Program Files\\X"',
        ],
    )
    def test_w1_fully_absolute_entries_are_searchable(self, entry: str) -> None:
        assert executable._is_searchable_path_entry(entry, windows=True)

    @pytest.mark.parametrize(
        "entry",
        [
            "",
            ".",
            "..",
            "bin",
            "..\\x",
            "\\bin",
            "/bin",
            "C:bin",
            "C:",
            "~\\bin",
            "%SystemRoot%\\system32",
            '""',
            '"bin"',
        ],
    )
    def test_w1_everything_else_is_skipped(self, entry: str) -> None:
        assert not executable._is_searchable_path_entry(entry, windows=True)

    @pytest.mark.parametrize(
        ("name", "extensions", "expected"),
        [
            ("rg", WINDOWS_NATIVE_EXTENSIONS, ["rg.exe", "rg.com"]),
            ("rg.exe", WINDOWS_NATIVE_EXTENSIONS, ["rg.exe"]),
            ("RG.EXE", WINDOWS_NATIVE_EXTENSIONS, ["RG.EXE"]),
            ("tool.com", WINDOWS_NATIVE_EXTENSIONS, ["tool.com"]),
            ("claude.cmd", WINDOWS_NATIVE_EXTENSIONS, []),
            ("claude.bat", WINDOWS_NATIVE_EXTENSIONS, []),
            ("tool.js", WINDOWS_NATIVE_EXTENSIONS, []),
            ("claude.cmd", (".exe", ".com", ".cmd", ".bat"), ["claude.cmd"]),
            ("claude", (".cmd", ".bat"), ["claude.cmd", "claude.bat"]),
        ],
    )
    def test_w2_candidate_names(
        self, name: str, extensions: tuple[str, ...], expected: list[str]
    ) -> None:
        got = executable._candidate_names(
            name, windows=True, windows_extensions=extensions
        )
        assert got == expected

    def test_posix_candidate_is_the_name_as_is(self) -> None:
        for name in ("rg", "rg.exe", "claude.cmd", "tool.js"):
            assert executable._candidate_names(
                name, windows=False, windows_extensions=WINDOWS_NATIVE_EXTENSIONS
            ) == [name]

    def test_candidate_paths_come_only_from_absolute_entries(self) -> None:
        path = ";".join(
            ["", ".", "plant", "C:plant", "\\plant", '"C:\\Program Files\\T"', "D:/t"]
        )
        got = list(
            executable._iter_candidate_paths(
                "tool",
                path,
                windows=True,
                windows_extensions=WINDOWS_NATIVE_EXTENSIONS,
            )
        )
        assert got == [
            "C:\\Program Files\\T\\tool.exe",
            "C:\\Program Files\\T\\tool.com",
            "D:\\t\\tool.exe",
            "D:\\t\\tool.com",
        ]

    @pytest.fixture
    def fake_windows(self, monkeypatch: pytest.MonkeyPatch) -> set[str]:
        """Run find_executable under the Windows rules against a fake
        filesystem: the returned set is what "exists"."""
        files: set[str] = set()
        monkeypatch.setattr(executable, "_is_windows", lambda: True)
        monkeypatch.setattr(
            executable,
            "_is_executable_file",
            lambda path, *, windows: windows and path in files,
        )
        return files

    def test_w3_planted_exe_in_cwd_is_never_found(self, fake_windows: set[str]) -> None:
        # The cwd is C:\plant. shutil.which would return C:\plant\tool.exe
        # for every one of these PATH values; find_executable never looks.
        fake_windows.update({"C:\\plant\\tool.exe", "C:\\bin\\tool.exe"})
        assert find_executable("tool", path=".;C:\\bin") == "C:\\bin\\tool.exe"
        assert find_executable("tool", path="C:\\bin;.") == "C:\\bin\\tool.exe"
        for cwd_only in ("", ".", ";;", ".;plant;C:plant;\\plant"):
            assert find_executable("tool", path=cwd_only) is None

    def test_w3_shim_in_earlier_directory_does_not_shadow_native_exe(
        self, fake_windows: set[str]
    ) -> None:
        # npm's directory (claude.cmd) and a git-bash wrapper (extensionless
        # claude) precede the native installer's directory on PATH.
        # shutil.which walks directory-major with PATHEXT and returns the
        # shim; G3 only ever considers claude.exe / claude.com.
        fake_windows.update(
            {
                "C:\\npm\\claude.cmd",
                "C:\\npm\\claude",
                "C:\\gitbash\\claude",
                "C:\\native\\claude.exe",
            }
        )
        path = 'C:\\npm;C:\\gitbash;"C:\\native"'
        assert find_executable("claude", path=path) == "C:\\native\\claude.exe"
        assert find_executable("claude.exe", path=path) == "C:\\native\\claude.exe"
        # The allow-list parameter lets a caller *detect* the shim (to
        # explain why it is refused) without it ever being a spawn candidate.
        shim = find_executable("claude", path=path, windows_extensions=(".cmd", ".bat"))
        assert shim == "C:\\npm\\claude.cmd"
        fake_windows.discard("C:\\native\\claude.exe")
        assert find_executable("claude", path=path) is None

    def test_explicit_path_detection(self) -> None:
        for name in ("C:\\x\\claude.exe", "x\\claude", "./claude", "C:claude"):
            assert executable._is_explicit_path(name, windows=True)
        for name in ("claude", "claude.exe"):
            assert not executable._is_explicit_path(name, windows=True)


class TestPosixRules:
    def test_p1_backslash_is_not_a_separator(self) -> None:
        assert not executable._is_explicit_path("a\\b", windows=False)
        assert executable._is_explicit_path("a/b", windows=False)

    @posix_only
    def test_p1_backslash_name_is_searched_as_a_bare_name(self, layout: Path) -> None:
        _make_executable(layout / "bin" / "a\\b")
        assert find_executable("a\\b", path=str(layout / "bin")) == str(
            layout / "bin" / "a\\b"
        )

    @pytest.mark.parametrize("entry", ["/usr/bin", "/", "//double"])
    def test_absolute_entries_are_searchable(self, entry: str) -> None:
        assert executable._is_searchable_path_entry(entry, windows=False)

    @pytest.mark.parametrize(
        "entry", ["", ".", "..", "bin", "./bin", "~/bin", "$HOME/bin", '"/quoted"']
    )
    def test_everything_else_is_skipped(self, entry: str) -> None:
        assert not executable._is_searchable_path_entry(entry, windows=False)


class TestRequireResolveRun:
    """The entry points built on find_executable."""

    def test_require_executable_raises_a_file_not_found_error(
        self, layout: Path
    ) -> None:
        assert require_executable("tool", path=str(layout / "bin")) == str(
            layout / "bin" / TOOL
        )
        with pytest.raises(ExecutableNotFoundError) as exc_info:
            require_executable("tool", path=".")
        assert isinstance(exc_info.value, FileNotFoundError)
        assert exc_info.value.name == "tool"
        assert exc_info.value.filename == "tool"
        assert "never the current directory" in str(exc_info.value)
        # Survives pickling (multiprocessing, xdist) despite OSError's
        # three-argument reconstruction protocol.
        clone = pickle.loads(pickle.dumps(exc_info.value))
        assert isinstance(clone, ExecutableNotFoundError) and clone.name == "tool"

    def test_resolve_argv_replaces_only_the_program(self, layout: Path) -> None:
        argv = resolve_argv(
            ["tool", "--flag", Path("rel/sub")], path=str(layout / "bin")
        )
        assert argv == [
            str(layout / "bin" / TOOL),
            "--flag",
            str(Path("rel", "sub")),
        ]
        with pytest.raises(ValueError):
            resolve_argv([], path=str(layout / "bin"))

    def test_run_resolves_then_spawns(self, layout: Path) -> None:
        # sys.executable is absolute, so this is the G4 pass-through.
        result = run(
            [sys.executable, "-c", "print('spawned')"], capture_output=True, text=True
        )
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.stdout.strip() == "spawned"

    @posix_only
    def test_run_searches_path_for_a_bare_name(
        self, layout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_executable(layout / "bin" / "tool", '#!/bin/sh\necho "from-bin:$1"\n')
        _make_executable(layout / "plant" / "tool", '#!/bin/sh\necho "PLANTED"\n')
        monkeypatch.setenv("PATH", _join(".", "", str(layout / "bin")))
        result = run(["tool", "x"], capture_output=True, text=True)
        assert result.stdout.strip() == "from-bin:x"
        assert result.args[0] == str(layout / "bin" / "tool")

    def test_run_never_spawns_a_plant(
        self, layout: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", _join(".", "", "rel/sub"))
        assert Path(TOOL).is_file()
        with pytest.raises(ExecutableNotFoundError):
            run(["tool"], capture_output=True)

    def test_run_refuses_shell_and_executable_overrides(self, layout: Path) -> None:
        with pytest.raises(TypeError):
            run([sys.executable, "-c", "pass"], shell=True)
        with pytest.raises(TypeError):
            run(["python", "-c", "pass"], executable=sys.executable)
