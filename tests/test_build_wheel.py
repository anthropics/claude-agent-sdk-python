"""Tests for scripts/build_wheel.py platform tagging and CLI-pin reading."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent

# scripts/ is not a package, so load build_wheel.py by path
_spec = importlib.util.spec_from_file_location(
    "build_wheel",
    REPO_ROOT / "scripts" / "build_wheel.py",
)
assert _spec is not None and _spec.loader is not None
build_wheel = importlib.util.module_from_spec(_spec)
sys.modules["build_wheel"] = build_wheel
_spec.loader.exec_module(build_wheel)


class TestGetPlatformTag:
    """Verify get_platform_tag() returns the correct wheel platform tag
    for every (system, machine) combination we publish."""

    @pytest.mark.parametrize(
        "system,machine,expected",
        [
            ("Darwin", "arm64", "macosx_11_0_arm64"),
            ("Darwin", "x86_64", "macosx_11_0_x86_64"),
            ("Linux", "x86_64", "manylinux_2_17_x86_64"),
            ("Linux", "amd64", "manylinux_2_17_x86_64"),
            ("Linux", "aarch64", "manylinux_2_17_aarch64"),
            ("Linux", "arm64", "manylinux_2_17_aarch64"),
            ("Windows", "AMD64", "win_amd64"),
            ("Windows", "x86_64", "win_amd64"),
            ("Windows", "ARM64", "win_arm64"),
        ],
    )
    def test_platform_tag(self, system: str, machine: str, expected: str) -> None:
        with (
            patch("platform.system", return_value=system),
            patch("platform.machine", return_value=machine),
        ):
            assert build_wheel.get_platform_tag() == expected

    def test_unknown_linux_arch_falls_through(self) -> None:
        """Unknown Linux arches should produce a generic linux_* tag."""
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="riscv64"),
        ):
            assert build_wheel.get_platform_tag() == "linux_riscv64"

    def test_unknown_system_falls_through(self) -> None:
        """Unknown systems should produce a lowercased system_arch tag."""
        with (
            patch("platform.system", return_value="FreeBSD"),
            patch("platform.machine", return_value="amd64"),
        ):
            assert build_wheel.get_platform_tag() == "freebsd_amd64"


@pytest.fixture
def pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in repo whose _cli_version.py the caller writes."""
    monkeypatch.chdir(tmp_path)
    version_file = tmp_path / build_wheel.CLI_VERSION_FILE
    version_file.parent.mkdir(parents=True)
    return version_file


class TestGetBundledCliVersion:
    """The CLI pin is the only record of which build goes into the wheels.

    An unreadable pin must fail the build: falling back to the moving "latest"
    -- which download_cli.py accepts -- would publish an unpinned set of wheels,
    each of the five release runners resolving it independently.
    """

    def test_reads_the_pinned_version(self, pinned: Path) -> None:
        pinned.write_text('__cli_version__ = "2.1.207"\n')
        assert build_wheel.get_bundled_cli_version() == "2.1.207"

    def test_reads_a_dev_version(self, pinned: Path) -> None:
        dev = "2.1.146-dev.20260519.t105443.shaece3dab"
        pinned.write_text(f'__cli_version__ = "{dev}"\n')
        assert build_wheel.get_bundled_cli_version() == dev

    def test_missing_file_fails(
        self, pinned: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            build_wheel.get_bundled_cli_version()

        assert excinfo.value.code == 1
        assert "does not exist" in capsys.readouterr().err

    def test_unparseable_pin_fails(
        self, pinned: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A single-quoted reformat is the realistic way the regex stops
        matching -- and used to silently downgrade the build to "latest"."""
        pinned.write_text("__cli_version__ = '2.1.207'\n")

        with pytest.raises(SystemExit) as excinfo:
            build_wheel.get_bundled_cli_version()

        assert excinfo.value.code == 1
        assert "no `" in capsys.readouterr().err

    @pytest.mark.parametrize("tag", ["latest", "stable"])
    def test_a_moving_dist_tag_pin_fails(
        self, pinned: Path, tag: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pinned.write_text(f'__cli_version__ = "{tag}"\n')

        with pytest.raises(SystemExit) as excinfo:
            build_wheel.get_bundled_cli_version()

        assert excinfo.value.code == 1
        assert "moving dist-tag" in capsys.readouterr().err

    @pytest.mark.parametrize("value", ["", "not-a-version", "v2.1.207", "2.1"])
    def test_a_bad_pin_fails(self, pinned: Path, value: str) -> None:
        pinned.write_text(f'__cli_version__ = "{value}"\n')

        with pytest.raises(SystemExit) as excinfo:
            build_wheel.get_bundled_cli_version()

        assert excinfo.value.code == 1

    def test_the_repo_pin_is_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pin actually checked in must satisfy the rule the build enforces."""
        monkeypatch.chdir(REPO_ROOT)
        assert build_wheel.get_bundled_cli_version() not in ("latest", "stable")


class TestSdistShipsTheScriptsItsTestsImport:
    """The sdist ships /tests, and these test modules exec their subject out of
    scripts/ at import time, so /scripts has to ship with them -- otherwise the
    shipped suite aborts at collection with FileNotFoundError."""

    def test_scripts_is_in_the_sdist(self) -> None:
        tomllib = pytest.importorskip("tomllib")  # stdlib from 3.11

        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        include = config["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]

        assert "/tests" in include
        assert "/scripts" in include
