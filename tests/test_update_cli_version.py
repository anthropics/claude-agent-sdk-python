"""Tests for scripts/update_cli_version.py version validation and file writing."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_cli_version.py"

# scripts/ is not a package, so load update_cli_version.py by path
_spec = importlib.util.spec_from_file_location("update_cli_version", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
update_cli_version = importlib.util.module_from_spec(_spec)
sys.modules["update_cli_version"] = update_cli_version
_spec.loader.exec_module(update_cli_version)

DEV_VERSION = "2.1.146-dev.20260519.t105443.shaece3dab"

ORIGINAL = '"""Bundled Claude Code CLI version."""\n\n__cli_version__ = "2.1.195"\n'


@pytest.fixture
def version_file(tmp_path: Path) -> Path:
    """A stand-in for src/claude_agent_sdk/_cli_version.py."""
    path = tmp_path / "_cli_version.py"
    path.write_text(ORIGINAL)
    return path


def import_version(path: Path) -> str:
    """Import the written file as Python and return its __cli_version__.

    Fails on a file that is not valid Python, which is the whole point: the
    value goes into a real source file that later gets imported.
    """
    spec = importlib.util.spec_from_file_location(f"_written_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version: str = module.__cli_version__
    return version


class TestAcceptedVersions:
    """Concrete versions round-trip through the file unchanged."""

    @pytest.mark.parametrize(
        "version",
        [
            "1.2.3",
            "2.1.195",
            "2.1.201",
            DEV_VERSION,
            "1.2.3-beta.1",
            "1.2.3-rc.1+build.4",
        ],
    )
    def test_round_trip(self, version_file: Path, version: str) -> None:
        update_cli_version.update_cli_version(version, version_file)
        assert import_version(version_file) == version

    @pytest.mark.parametrize(
        ("argument", "written"),
        [
            ("1.2.3\n", "1.2.3"),
            ("1.2.3\r\n", "1.2.3"),
            ("  2.1.201  ", "2.1.201"),
            (f"{DEV_VERSION}\n", DEV_VERSION),
        ],
    )
    def test_surrounding_whitespace_is_stripped_before_writing(
        self, version_file: Path, argument: str, written: str
    ) -> None:
        """The stripped value is written, so a trailing newline from a file read
        never lands inside the string literal."""
        update_cli_version.update_cli_version(argument, version_file)
        assert import_version(version_file) == written

    def test_docstring_and_shape_preserved(self, version_file: Path) -> None:
        update_cli_version.update_cli_version(DEV_VERSION, version_file)
        assert version_file.read_text() == ORIGINAL.replace("2.1.195", DEV_VERSION)

    def test_only_first_assignment_replaced(self, tmp_path: Path) -> None:
        path = tmp_path / "_cli_version.py"
        path.write_text('__cli_version__ = "1.0.0"\n_other = "1.0.0"\n')
        update_cli_version.update_cli_version("2.0.0", path)
        assert path.read_text() == '__cli_version__ = "2.0.0"\n_other = "1.0.0"\n'


class TestRejectedVersions:
    """Invalid input raises before the file is touched."""

    @pytest.mark.parametrize(
        "version",
        [
            # Quote breakout: closes the string literal in a real source file.
            '1.0.0"',
            '1.0.0" + __import__("os").system("id") + "',
            '"',
            # Backslashes: re.sub() replacement-escape processing, and invalid
            # Python escapes in the emitted literal.
            "1.0.0\\",
            "\\",
            "1.0.0\\1",
            "1.0.0\\g<0>",
            "\\g<0>",
            "1.0.0\\n",
            # Newlines *inside* the value: an extra source line in the
            # generated module. (A merely trailing newline is stripped -- see
            # TestAcceptedVersions.)
            "1.0.0\nimport os",
            "\n",
            # Empty and flag-shaped.
            "",
            "-1.2.3",
            "--help",
            "-s",
            # Leftovers from the shell-injection allowlist.
            "1.0.0; id",
            "$(id)",
            "`id`",
            "1.0.0 2.0.0",
            " 1.0.0 2.0.0 ",
            "../../etc/passwd",
            ".1.2.3",
            # Not versions at all: the old allowlist took these for concrete
            # versions and pinned them into the file, deferring the failure to
            # the wheel build.
            "0",
            "1.2",
            "1.2.3.4",
            "1.2.3+build.4",
            "v2.1.207",
            "next",
            "beta",
            # Case variants of the dist-tags: the installer's grammar is
            # case-sensitive, so these are not tags it would resolve either.
            "LATEST",
            "Stable",
        ],
    )
    def test_rejected_and_file_untouched(
        self, version_file: Path, version: str
    ) -> None:
        with pytest.raises(ValueError, match="Invalid CLI version"):
            update_cli_version.update_cli_version(version, version_file)
        assert version_file.read_text() == ORIGINAL

    @pytest.mark.parametrize("tag", ["latest", "stable"])
    def test_dist_tags_are_rejected(self, version_file: Path, tag: str) -> None:
        """_cli_version.py must name one concrete build, never a moving tag.

        Five release runners each run build_wheel.py -> download_cli.py with
        this value, so a dist-tag would let them resolve different CLI builds
        into wheels published under one SDK version. "stable" is the dangerous
        one: it passes the installer too, so before this guard it pinned a
        moving tag and the build *succeeded*.
        """
        with pytest.raises(ValueError, match="Invalid CLI version") as excinfo:
            update_cli_version.update_cli_version(tag, version_file)

        assert "moving dist-tag" in str(excinfo.value)
        assert "not a concrete version" in str(excinfo.value)
        assert version_file.read_text() == ORIGINAL

    def test_leading_v_is_named_not_normalized(self, version_file: Path) -> None:
        """It reached the file before, and failed at wheel-build time."""
        with pytest.raises(ValueError) as excinfo:
            update_cli_version.update_cli_version("v2.1.207", version_file)

        assert "Did you mean '2.1.207'?" in str(excinfo.value)
        assert version_file.read_text() == ORIGINAL

    def test_unsupported_dist_tag_is_named(self, version_file: Path) -> None:
        with pytest.raises(ValueError, match="not a supported dist-tag"):
            update_cli_version.update_cli_version("next", version_file)
        assert version_file.read_text() == ORIGINAL

    def test_error_names_the_offending_value(self, version_file: Path) -> None:
        with pytest.raises(ValueError) as excinfo:
            update_cli_version.update_cli_version('1.0.0"; id', version_file)
        assert '1.0.0"; id' in str(excinfo.value)

    def test_missing_assignment_leaves_file_untouched(self, tmp_path: Path) -> None:
        path = tmp_path / "_cli_version.py"
        path.write_text("# no assignment here\n")
        with pytest.raises(ValueError, match="No __cli_version__ assignment"):
            update_cli_version.update_cli_version("1.2.3", path)
        assert path.read_text() == "# no assignment here\n"


class TestReplacementIsLiteral:
    """The version reaches the file verbatim, with no escape interpretation.

    Validation already excludes every character these tests use, so they stub
    it out to exercise the write path directly. Without that, a plain-string
    re.sub() replacement -- which expands \\1 and \\g<0>, turns \\n into a
    newline, and raises on a bare trailing backslash -- would look identical to
    the callable replacement for all reachable input, and the guard would be
    unfalsifiable. This is the test that fails if someone swaps the callable
    back for an f-string.
    """

    @pytest.fixture
    def no_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def accept(version: str, *, source: str, allow_dist_tag: bool) -> str:
            return version

        monkeypatch.setattr(
            update_cli_version.version_validation, "validate_version", accept
        )

    @pytest.mark.usefixtures("no_validation")
    @pytest.mark.parametrize(
        "version",
        [
            "2.0.0\\1",
            "2.0.0\\g<0>",
            "2.0.0\\",
            "\\g<0>\\1",
            '2.0.0"',
            "2.0.0\\n",
            "2.0.0\nid",
        ],
    )
    def test_written_file_imports_back_to_the_exact_string(
        self, version_file: Path, version: str
    ) -> None:
        update_cli_version.update_cli_version(version, version_file)
        assert import_version(version_file) == version

    @pytest.mark.usefixtures("no_validation")
    def test_backreference_is_not_expanded_into_the_file(
        self, version_file: Path
    ) -> None:
        """A string replacement would splice the matched assignment into itself."""
        update_cli_version.update_cli_version("2.0.0\\g<0>", version_file)
        text = version_file.read_text()
        assert text.count("__cli_version__") == 1

    def test_replacement_is_a_callable(
        self, monkeypatch: pytest.MonkeyPatch, version_file: Path
    ) -> None:
        """Guard the mechanism itself, not only its observable output."""
        spy = _SubnSpy(update_cli_version.ASSIGNMENT_PATTERN)
        monkeypatch.setattr(update_cli_version, "ASSIGNMENT_PATTERN", spy)

        update_cli_version.update_cli_version("1.2.3", version_file)

        (repl,) = spy.replacements
        assert callable(repl), (
            f"re.sub replacement must be a callable to avoid escape processing, "
            f"got {repl!r}"
        )


class _SubnSpy:
    """Records the replacement handed to Pattern.subn(), then delegates."""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._pattern = pattern
        self.replacements: list[object] = []

    def subn(self, repl: object, string: str, count: int = 0) -> tuple[str, int]:
        self.replacements.append(repl)
        return self._pattern.subn(repl, string, count=count)  # type: ignore[arg-type]


class TestCommandLine:
    """The script still works when run directly, with scripts/ not a package.

    Every case runs in a throwaway cwd holding a copy of the real
    src/claude_agent_sdk/_cli_version.py layout, so a regression in the guard
    corrupts the fixture rather than the repository.
    """

    @pytest.fixture
    def cwd(self, tmp_path: Path) -> Path:
        target = tmp_path / "src" / "claude_agent_sdk" / "_cli_version.py"
        target.parent.mkdir(parents=True)
        target.write_text(ORIGINAL)
        return tmp_path

    def _run(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def _target(self, cwd: Path) -> Path:
        return cwd / "src" / "claude_agent_sdk" / "_cli_version.py"

    def test_invalid_version_exits_nonzero_without_writing(self, cwd: Path) -> None:
        result = self._run(cwd, '9.9.9"; import os')

        assert result.returncode == 1
        assert "Invalid CLI version" in result.stderr
        assert result.stdout == ""
        assert self._target(cwd).read_text() == ORIGINAL

    @pytest.mark.parametrize("tag", ["latest", "stable"])
    def test_dist_tag_exits_nonzero_without_writing(self, cwd: Path, tag: str) -> None:
        """`stable` used to pin cleanly and build cleanly -- against whatever
        the tag happened to point at that day."""
        result = self._run(cwd, tag)

        assert result.returncode == 1
        assert "not a concrete version" in result.stderr
        assert self._target(cwd).read_text() == ORIGINAL

    def test_leading_v_exits_nonzero_without_writing(self, cwd: Path) -> None:
        result = self._run(cwd, "v2.1.207")

        assert result.returncode == 1
        assert "Did you mean '2.1.207'?" in result.stderr
        assert self._target(cwd).read_text() == ORIGINAL

    def test_missing_argument_prints_usage_to_stderr(self, cwd: Path) -> None:
        result = self._run(cwd)
        assert result.returncode == 1
        assert "Usage:" in result.stderr

    def test_writes_when_run_directly(self, cwd: Path) -> None:
        """Direct execution must resolve the shared validation module."""
        result = self._run(cwd, DEV_VERSION)

        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr
        assert import_version(self._target(cwd)) == DEV_VERSION
