"""Tests for scripts/update_version.py version validation and substitution.

The version handed to this script is written into two source files as a string
literal -- a TOML one in pyproject.toml, a Python one in _version.py -- so it
is code, not data. These tests pin the two properties that keep it from being
reinterpreted as such:

  * the allowlist admits no quote, backslash, newline, or shell metacharacter,
    so a value cannot break out of the literal it is written into; and
  * the re.sub() replacement is a *callable*, so a backslash escape in the
    value ("\\1", "\\g<0>") is inserted literally rather than expanded against
    the match.

They also pin the converse, which is the one that bites on release day: every
version this project has actually released must still validate.
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "update_version.py"

# scripts/ is not a package, so load update_version.py by path (same approach
# as tests/test_build_wheel.py).
_spec = importlib.util.spec_from_file_location("update_version", _SCRIPT)
assert _spec is not None and _spec.loader is not None
update_version_module = importlib.util.module_from_spec(_spec)
sys.modules["update_version"] = update_version_module
_spec.loader.exec_module(update_version_module)

validate_version = update_version_module.validate_version
update_version = update_version_module.update_version
VERSION_PATTERN = update_version_module.VERSION_PATTERN


# Every version this project has ever published: `git tag` and the PyPI
# release list agree, 121 of them, all plain MAJOR.MINOR.PATCH. A sample
# spanning the whole history, plus the PEP 440 suffixes a future release could
# legitimately use. Rejecting any of these would break a real release.
REAL_VERSIONS = [
    "0.0.16",
    "0.0.25",
    "0.1.0",
    "0.1.9",
    "0.1.81",
    "0.2.82",
    "0.2.100",
    "0.2.117",
    "0.2.118",
    "0.2.119",
]

FUTURE_VALID_VERSIONS = [
    "1.0.0",
    "10.20.30",
    "0.3.0a1",
    "0.3.0b2",
    "0.3.0rc1",
    "0.3.0.post1",
    "0.3.0.dev1",
    "0.3.0rc1.post2.dev3",
]


class TestAcceptsRealVersions:
    """The allowlist must not reject a version this project could publish."""

    @pytest.mark.parametrize("version", REAL_VERSIONS)
    def test_every_released_version_validates(self, version: str) -> None:
        assert validate_version(version) == version

    @pytest.mark.parametrize("version", FUTURE_VALID_VERSIONS)
    def test_pep440_suffixed_versions_validate(self, version: str) -> None:
        assert validate_version(version) == version

    def test_every_git_tag_validates(self) -> None:
        """The real tag list, read from git -- not a hand-copied sample.

        Skipped where git history is unavailable (e.g. a shallow checkout or
        an sdist), since the parametrized sample above still covers the shapes.
        """
        try:
            result = subprocess.run(
                ["git", "tag", "--list", "v*"],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
            pytest.skip("git tags unavailable")

        tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
        if not tags:  # pragma: no cover
            pytest.skip("no tags in this checkout")

        for tag in tags:
            version = tag[1:]  # strip the leading "v"
            assert validate_version(version) == version, f"rejected released {tag}"

    def test_surrounding_whitespace_is_stripped(self) -> None:
        """A trailing newline from a file read is unambiguous in intent."""
        assert validate_version("  0.2.119\n") == "0.2.119"


class TestRejectsInjection:
    """The allowlist must admit nothing that could escape a string literal."""

    @pytest.mark.parametrize(
        "version",
        [
            # Quote breakout -- the hazard that makes _version.py executable.
            '0.2.119"',
            '0.2.119" + __import__("os").system("id") + "',
            '0.2.119"\nimport os\nos.system("id")\n#',
            "0.2.119'",
            # Backslashes: rejected by the pattern *and* neutered by the
            # callable replacement (see TestCallableReplacementIsLiteral).
            "0.2.119\\",
            "0.2.119\\n",
            # Shell metacharacters -- this value also reaches a shell in CI.
            "0.2.119; id",
            "0.2.119 && id",
            "0.2.119 | id",
            "0.2.119`id`",
            "0.2.119$(id)",
            "0.2.119${IFS}id",
            "0.2.119 #comment",
            # Control characters and interior newlines. (A *trailing* newline
            # is stripped and accepted -- see test_surrounding_whitespace.)
            "0.2.119\n0.0.0",
            "0.2.119\r\n0.0.0",
            "0.2.119\x00",
            "0.2.119\x1b[31m",
            "0.2.119\t0.0.0",
            # Interior whitespace is not stripped, and is not a version.
            "0.2. 119",
            "0 .2.119",
            # Not a version at all.
            "",
            "   ",
            "latest",
            "stable",
            "0.2",
            "0.2.119.4.5",
            "0.2.x",
            "-0.2.119",
            "../../etc/passwd",
            "0.2.119/../../evil",
        ],
    )
    def test_rejected(self, version: str) -> None:
        with pytest.raises(ValueError):
            validate_version(version)

    def test_leading_v_is_rejected_with_a_useful_message(self) -> None:
        """The tags carry a "v"; the version must not. Say so."""
        with pytest.raises(ValueError, match=r"no leading 'v'"):
            validate_version("v0.2.119")

    def test_error_message_names_the_source(self) -> None:
        with pytest.raises(ValueError, match="CUSTOM_SOURCE"):
            validate_version("nope", source="CUSTOM_SOURCE")

    def test_pattern_is_fullmatched_not_prefix_matched(self) -> None:
        """A valid prefix must not carry an arbitrary suffix along with it."""
        assert VERSION_PATTERN.match("0.2.119; id") is not None  # prefix matches
        assert VERSION_PATTERN.fullmatch("0.2.119; id") is None  # but not the whole


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal checkout: the two files update_version() rewrites."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "claude-agent-sdk"\nversion = "0.2.119"\n'
        '\n[tool.other]\nversion = "9.9.9"\n'  # must not be touched (count=1)
    )
    version_dir = tmp_path / "src" / "claude_agent_sdk"
    version_dir.mkdir(parents=True)
    (version_dir / "_version.py").write_text('__version__ = "0.2.119"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestUpdateVersion:
    """End-to-end: what actually lands in the two files."""

    def test_writes_both_files(self, repo: Path) -> None:
        update_version("0.2.120")
        assert 'version = "0.2.120"' in (repo / "pyproject.toml").read_text()
        assert (
            repo / "src" / "claude_agent_sdk" / "_version.py"
        ).read_text() == '__version__ = "0.2.120"\n'

    def test_only_the_first_version_field_is_rewritten(self, repo: Path) -> None:
        """count=1: the [tool.other] version stays put."""
        update_version("0.2.120")
        assert 'version = "9.9.9"' in (repo / "pyproject.toml").read_text()

    def test_written_version_py_is_importable_and_correct(self, repo: Path) -> None:
        """The whole point: _version.py must still be a sane Python module."""
        update_version("0.3.0rc1")
        namespace: dict[str, object] = {}
        exec(  # noqa: S102 - executing the file we just generated is the test
            (repo / "src" / "claude_agent_sdk" / "_version.py").read_text(),
            namespace,
        )
        assert namespace["__version__"] == "0.3.0rc1"


class TestRejectionLeavesFilesUntouched:
    """A rejected version must not modify anything."""

    @pytest.mark.parametrize(
        "version",
        ['0.2.119"; import os', "0.2.119; id", "\\1", "latest", "v0.2.119"],
    )
    def test_no_file_is_modified(self, repo: Path, version: str) -> None:
        pyproject = repo / "pyproject.toml"
        version_py = repo / "src" / "claude_agent_sdk" / "_version.py"
        before = (pyproject.read_text(), version_py.read_text())

        with pytest.raises(ValueError):
            update_version(version)

        assert (pyproject.read_text(), version_py.read_text()) == before


class TestCallableReplacementIsLiteral:
    """re.sub() with a *string* replacement runs it through backslash-escape
    processing; with a callable it does not.

    The allowlist already rejects every payload below, so these are defense in
    depth -- they pin the substitution layer independently, so that widening
    the pattern (or reusing _substitute elsewhere) cannot silently reintroduce
    backreference expansion.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            r"\1",
            r"\g<0>",
            r"\g<1>",
            "\\",
            r"\n",
            r"0.2.119\1",
        ],
    )
    def test_payload_is_inserted_literally(self, tmp_path: Path, payload: str) -> None:
        target = tmp_path / "pyproject.toml"
        target.write_text('version = "0.0.0"\n')

        result = update_version_module._substitute(
            target, r'^version = "[^"]*"', "version", payload
        )

        # The value survives verbatim, JSON-quoted -- no group expansion, no
        # "bad escape" error, no bare backslash mangling.
        assert result == f"version = {json.dumps(payload)}\n"
        assert json.loads(result.split(" = ", 1)[1].strip()) == payload

    def test_string_replacement_would_have_expanded_it(self) -> None:
        """The bug this guards against, demonstrated on the old code shape.

        With a string replacement, "\\g<0>" in the version is expanded to the
        whole match instead of being written out -- proof the callable is
        load-bearing and not decoration.
        """
        content = 'version = "0.0.0"\n'
        payload = r"\g<0>"

        # The old shape: an f-string spliced into a *string* replacement.
        expanded = re.sub(
            r'^version = "[^"]*"',
            f'version = "{payload}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        assert expanded == 'version = "version = "0.0.0""\n'  # group expanded!
        assert payload not in expanded

        # The new shape keeps it literal.
        kept = re.sub(
            r'^version = "[^"]*"',
            lambda _m: f'version = "{payload}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        assert kept == 'version = "\\g<0>"\n'

    def test_missing_anchor_raises_instead_of_silently_succeeding(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "pyproject.toml"
        target.write_text("name = 'no version here'\n")

        with pytest.raises(ValueError, match="Could not find"):
            update_version_module._substitute(
                target, r'^version = "[^"]*"', "version", "0.2.120"
            )


class TestCommandLine:
    """The script must fail cleanly -- non-zero, one line on stderr, no traceback."""

    def _run(self, arg: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), arg],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

    def test_rejects_injection_with_clean_exit(self, repo: Path) -> None:
        result = self._run('0.2.119"; import os', repo)

        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "Invalid version" in result.stderr
        assert result.stderr.strip().count("\n") == 0  # one line
        # And nothing was written.
        assert 'version = "0.2.119"' in (repo / "pyproject.toml").read_text()

    def test_accepts_a_real_version(self, repo: Path) -> None:
        result = self._run("0.2.120", repo)

        assert result.returncode == 0, result.stderr
        assert 'version = "0.2.120"' in (repo / "pyproject.toml").read_text()

    def test_usage_error_without_traceback(self, repo: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            cwd=repo,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "Usage:" in result.stderr
