"""Tests for scripts/build_wheel.py platform tagging."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# scripts/ is not a package, so load build_wheel.py by path
_spec = importlib.util.spec_from_file_location(
    "build_wheel",
    Path(__file__).parent.parent / "scripts" / "build_wheel.py",
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
