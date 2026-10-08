#!/usr/bin/env python3
"""Build wheel with bundled Claude Code CLI.

This script handles the complete wheel building process:
1. Optionally updates version
2. Downloads Claude Code CLI
3. Builds the wheel
4. Optionally cleans up the bundled CLI

Usage:
    python scripts/build_wheel.py                    # Build with current version
    python scripts/build_wheel.py --version 0.1.4    # Build with specific version
    python scripts/build_wheel.py --clean            # Clean bundled CLI after build
    python scripts/build_wheel.py --skip-download    # Skip CLI download (use existing)
"""

import argparse
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path
from typing import NoReturn

# scripts/ is not a package; see the note in download_cli.py.
_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.append(_SCRIPTS_DIR)

import _cli_version_validation as version_validation  # noqa: E402

try:
    import twine  # noqa: F401

    HAS_TWINE = True
except ImportError:
    HAS_TWINE = False


def run_command(cmd: list[str], description: str) -> None:
    """Run a command and handle errors."""
    print(f"\n{'=' * 60}")
    print(f"{description}")
    print(f"{'=' * 60}")
    print(f"$ {' '.join(cmd)}")
    print()

    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error: {description} failed", file=sys.stderr)
        print(e.stdout, file=sys.stderr)
        sys.exit(1)


def update_version(version: str) -> None:
    """Update package version."""
    script_dir = Path(__file__).parent
    update_script = script_dir / "update_version.py"

    if not update_script.exists():
        print("Warning: update_version.py not found, skipping version update")
        return

    run_command(
        [sys.executable, str(update_script), version],
        f"Updating version to {version}",
    )


CLI_VERSION_FILE = Path("src/claude_agent_sdk/_cli_version.py")

# The assignment update_cli_version.py writes.
CLI_VERSION_PATTERN = re.compile(r'__cli_version__ = "([^"]+)"')


def _fail_unpinned(reason: str) -> NoReturn:
    """Report an unusable CLI pin and stop the build."""
    print(
        f"Error: cannot determine the CLI version to bundle: {reason}", file=sys.stderr
    )
    print(
        f"Expected a concrete version in {CLI_VERSION_FILE} "
        f'(written as `__cli_version__ = "2.1.207"`). '
        f"Fix the pin, or pass --cli-version explicitly.",
        file=sys.stderr,
    )
    sys.exit(1)


def get_bundled_cli_version() -> str:
    """Get the CLI version that should be bundled from _cli_version.py.

    Fails the build rather than falling back to a dist-tag: _cli_version.py is
    the only record of which CLI build goes into the wheels, and each of the
    release matrix's runners would resolve a moving "latest" independently.
    """
    if not CLI_VERSION_FILE.exists():
        _fail_unpinned(f"{CLI_VERSION_FILE} does not exist")

    match = CLI_VERSION_PATTERN.search(CLI_VERSION_FILE.read_text())
    if not match:
        _fail_unpinned(
            f"no `{CLI_VERSION_PATTERN.pattern}` assignment found in {CLI_VERSION_FILE}"
        )

    try:
        return version_validation.validate_version(
            match.group(1), source=str(CLI_VERSION_FILE), allow_dist_tag=False
        )
    except ValueError as exc:
        _fail_unpinned(str(exc))


def download_cli(cli_version: str | None = None) -> None:
    """Download Claude Code CLI."""
    # Use provided version, or fall back to version from _cli_version.py
    if cli_version is None:
        cli_version = get_bundled_cli_version()

    script_dir = Path(__file__).parent
    download_script = script_dir / "download_cli.py"

    # Set environment variable for download script
    os.environ["CLAUDE_CLI_VERSION"] = cli_version

    run_command(
        [sys.executable, str(download_script)],
        f"Downloading Claude Code CLI ({cli_version})",
    )


def clean_dist() -> None:
    """Clean dist directory."""
    dist_dir = Path("dist")
    if dist_dir.exists():
        print(f"\n{'=' * 60}")
        print("Cleaning dist directory")
        print(f"{'=' * 60}")
        shutil.rmtree(dist_dir)
        print("Cleaned dist/")


def get_platform_tag() -> str:
    """Get the appropriate platform tag for the current platform.

    Uses minimum compatible versions for broad compatibility:
    - macOS: 11.0 (Big Sur) as minimum
    - Linux: manylinux_2_17 (widely compatible)
    - Windows: Standard tags
    """
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Darwin":
        # macOS - use minimum version 11.0 (Big Sur) for broad compatibility
        if machine == "arm64":
            return "macosx_11_0_arm64"
        else:
            return "macosx_11_0_x86_64"
    elif system == "Linux":
        # Linux - use manylinux for broad compatibility
        if machine in ["x86_64", "amd64"]:
            return "manylinux_2_17_x86_64"
        elif machine in ["aarch64", "arm64"]:
            return "manylinux_2_17_aarch64"
        else:
            return f"linux_{machine}"
    elif system == "Windows":
        # Windows
        if machine in ["x86_64", "amd64"]:
            return "win_amd64"
        elif machine == "arm64":
            return "win_arm64"
        else:
            return "win32"
    else:
        # Unknown platform, use generic
        return f"{system.lower()}_{machine}"


def retag_wheel(wheel_path: Path, platform_tag: str) -> Path:
    """Retag a wheel with the correct platform tag using wheel package."""
    print(f"\n{'=' * 60}")
    print("Retagging wheel as platform-specific")
    print(f"{'=' * 60}")
    print(f"Old: {wheel_path.name}")

    # Use wheel package to properly retag (updates both filename and metadata)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "wheel",
            "tags",
            "--platform-tag",
            platform_tag,
            "--remove",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Warning: Failed to retag wheel: {result.stderr}")
        return wheel_path

    # Find the newly tagged wheel
    dist_dir = wheel_path.parent
    # The wheel package creates a new file with the platform tag
    new_wheels = list(dist_dir.glob(f"*{platform_tag}.whl"))

    if new_wheels:
        new_path = new_wheels[0]
        print(f"New: {new_path.name}")
        print("Wheel retagged successfully")

        # Remove the old wheel
        if wheel_path.exists() and wheel_path != new_path:
            wheel_path.unlink()

        return new_path
    else:
        print("Warning: Could not find retagged wheel")
        return wheel_path


RECOMPRESS_ENCODERS = ("zlib", "zopfli")

# 5 iterations is the zopfli package's advice for files over several MB: more is too
# slow. Up to 120 blocks per megabyte, not the default 15: each block gets its own
# Huffman code, which suits a binary whose sections differ. Raising either slows the
# build.
ZOPFLI_OPTIONS = {"numiterations": 5, "blocksplittingmax": 120}

# Zip record layouts, from PKWARE's APPNOTE.TXT sections 4.3.7, 4.3.12 and 4.3.16.
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_END_RECORD = struct.Struct("<4s4H2LH")


def _fail_recompress(reason: str) -> NoReturn:
    """Report a wheel that could not be recompressed and stop the build."""
    print(f"Error: cannot recompress the wheel: {reason}", file=sys.stderr)
    sys.exit(1)


def require_encoder(encoder: str) -> None:
    """Stop the build if the encoder needs a package that is not installed."""
    if encoder != "zopfli":
        return
    try:
        import zopfli.zlib  # noqa: F401
    except ImportError:
        _fail_recompress(
            "--recompress zopfli needs the zopfli package. Install it with: "
            "pip install --require-hashes --only-binary :all: "
            "-r scripts/requirements-recompress.txt"
        )


def _deflate(data: bytes, encoder: str) -> bytes:
    """Return data as one raw deflate stream, the form a zip member holds."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    best = compressor.compress(data) + compressor.flush()
    if encoder == "zopfli":
        import zopfli.zlib

        # zopfli returns a zlib container (RFC 1950): a 2-byte header, the raw
        # deflate stream, and a 4-byte checksum.
        container = zopfli.zlib.compress(data, **ZOPFLI_OPTIONS)
        if container[0] & 0x0F != 8 or container[1] & 0x20:
            _fail_recompress(
                f"zopfli {zopfli.__version__} produced output that is not in the zlib "
                "format this script expects. Check that it is the version pinned in "
                "scripts/requirements-recompress.txt"
            )
        if len(container) - 6 < len(best):
            best = container[2:-4]
    return best


def _member_fields(info: zipfile.ZipInfo) -> tuple[object, ...]:
    """Everything about a member that recompressing must leave as it was."""
    return (
        info.filename,
        info.date_time,
        info.compress_type,
        info.CRC,
        info.file_size,
        info.external_attr,
        info.internal_attr,
        info.create_system,
        info.create_version,
        info.extract_version,
        info.flag_bits & ~0x08,
        info.extra,
        info.comment,
    )


def _same_members(old: Path, new: Path) -> bool:
    """True if both zips hold the same members in the same order and contents."""
    try:
        with zipfile.ZipFile(old) as old_zip, zipfile.ZipFile(new) as new_zip:
            old_infos, new_infos = old_zip.infolist(), new_zip.infolist()
            if len(old_infos) != len(new_infos) or old_zip.comment != new_zip.comment:
                return False
            for old_info, new_info in zip(old_infos, new_infos, strict=True):
                if _member_fields(old_info) != _member_fields(new_info):
                    return False
                if old_zip.read(old_info) != new_zip.read(new_info):
                    return False
    except (zipfile.BadZipFile, zlib.error):
        # A member that does not decompress, or fails its CRC check.
        return False
    return True


def _write_recompressed(wheel_path: Path, tmp_path: Path, encoder: str) -> None:
    """Write wheel_path's members to tmp_path, deflated again with the encoder."""
    # zipfile cannot take bytes that are already compressed, so the records are
    # written here directly.
    with zipfile.ZipFile(wheel_path) as wheel, tmp_path.open("wb") as out:
        infos = wheel.infolist()
        if len(infos) >= 0xFFFF:
            _fail_recompress(
                f"the wheel holds {len(infos):,} files, and this script can only "
                "rewrite fewer than 65,535 (it does not write the ZIP64 format)"
            )
        central_directory = []
        for info in infos:
            if info.flag_bits & 0x01:
                _fail_recompress(
                    f"{info.filename} is encrypted, and this script cannot rewrite "
                    "encrypted files"
                )
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                _fail_recompress(
                    f"{info.filename} uses zip compression method "
                    f"{info.compress_type}, and this script can only rewrite files that "
                    "are stored (0) or deflated (8)"
                )
            data = wheel.read(info)
            if info.compress_type == zipfile.ZIP_DEFLATED:
                payload = _deflate(data, encoder)
            else:
                payload = data
            offset = out.tell()
            if max(offset, len(payload), len(data)) >= 0xFFFFFFFF:
                _fail_recompress(
                    f"the wheel reaches 4 GiB at {info.filename}. A zip needs the ZIP64 "
                    "format for that, and this script does not write it"
                )
            # Bit 3 says the sizes follow the data. Here they are in the header.
            flags = info.flag_bits & ~0x08
            name = info.filename.encode("utf-8" if flags & 0x800 else "cp437")
            year, month, day, hour, minute, second = info.date_time
            dos_date = (year - 1980) << 9 | month << 5 | day
            dos_time = hour << 11 | minute << 5 | second // 2
            shared = (
                info.extract_version,
                flags,
                info.compress_type,
                dos_time,
                dos_date,
                info.CRC,
                len(payload),
                len(data),
                len(name),
                len(info.extra),
            )
            out.write(_LOCAL_HEADER.pack(b"PK\x03\x04", *shared) + name + info.extra)
            out.write(payload)
            central_directory.append(
                _CENTRAL_HEADER.pack(
                    b"PK\x01\x02",
                    info.create_system << 8 | info.create_version,
                    *shared,
                    len(info.comment),
                    0,
                    info.internal_attr,
                    info.external_attr,
                    offset,
                )
                + name
                + info.extra
                + info.comment
            )
        directory_offset = out.tell()
        directory = b"".join(central_directory)
        if directory_offset + len(directory) >= 0xFFFFFFFF:
            _fail_recompress(
                "the rewritten wheel would be 4 GiB or larger. A zip needs the ZIP64 "
                "format for that, and this script does not write it"
            )
        out.write(directory)
        out.write(
            _END_RECORD.pack(
                b"PK\x05\x06",
                0,
                0,
                len(infos),
                len(infos),
                len(directory),
                directory_offset,
                len(wheel.comment),
            )
            + wheel.comment
        )


def recompress_wheel(wheel_path: Path, encoder: str) -> None:
    """Rewrite a wheel in place with its deflated members compressed harder.

    Member names, order, contents, dates and file modes stay as they were, so the
    hashes in the wheel's RECORD file still match. Exits with status 1, leaving the
    wheel untouched, if a member cannot be rewritten or the result does not match.
    """
    note = " (takes minutes, prints nothing until done)" if encoder == "zopfli" else ""
    print(f"\n{'=' * 60}")
    print(f"Recompressing wheel with {encoder}{note}")
    print(f"{'=' * 60}", flush=True)
    started = time.monotonic()
    old_size = wheel_path.stat().st_size
    tmp_path = wheel_path.with_name(wheel_path.name + ".tmp")

    try:
        _write_recompressed(wheel_path, tmp_path, encoder)
        if not _same_members(wheel_path, tmp_path):
            _fail_recompress(
                f"the rewritten copy of {wheel_path.name} does not hold the same files "
                "as the original, so the original was kept. This is a fault in "
                "build_wheel.py's zip writing or in the encoder, not in the wheel"
            )
        tmp_path.replace(wheel_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    new_size = wheel_path.stat().st_size
    if new_size < old_size:
        change = f"{(old_size - new_size) / 1024**2:.2f} MiB smaller"
    elif new_size == old_size:
        change = "no change"
    else:
        change = f"{new_size - old_size:,} bytes larger"
    print(
        f"{wheel_path.name}: {old_size:,} -> {new_size:,} bytes ({change}) "
        f"in {time.monotonic() - started:.0f} s"
    )


def build_wheel(recompress: str | None = None) -> None:
    """Build the wheel."""
    run_command(
        [sys.executable, "-m", "build", "--wheel"],
        "Building wheel",
    )

    # Check if we have a bundled CLI - if so, retag the wheel as platform-specific
    bundled_cli = Path("src/claude_agent_sdk/_bundled/claude")
    bundled_cli_exe = Path("src/claude_agent_sdk/_bundled/claude.exe")

    if bundled_cli.exists() or bundled_cli_exe.exists():
        # Find the built wheel
        dist_dir = Path("dist")
        wheels = list(dist_dir.glob("*.whl"))

        if wheels:
            # Get platform tag
            platform_tag = get_platform_tag()

            # Retag each wheel (should only be one)
            for wheel in wheels:
                if "-any.whl" in wheel.name:
                    retag_wheel(wheel, platform_tag)
        else:
            print("Warning: No wheel found to retag")
    else:
        print("\nNo bundled CLI found - wheel will be platform-independent")

    if recompress:
        for wheel in sorted(Path("dist").glob("*.whl")):
            recompress_wheel(wheel, recompress)


def build_sdist() -> None:
    """Build the source distribution."""
    run_command(
        [sys.executable, "-m", "build", "--sdist"],
        "Building source distribution",
    )


def check_package() -> None:
    """Check package with twine."""
    if not HAS_TWINE:
        print("\nWarning: twine not installed, skipping package check")
        print("Install with: pip install twine")
        return

    print(f"\n{'=' * 60}")
    print("Checking package with twine")
    print(f"{'=' * 60}")
    print(f"$ {sys.executable} -m twine check dist/*")
    print()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "twine", "check", "dist/*"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(result.stdout)

        if result.returncode != 0:
            print("\nWarning: twine check reported issues")
            print("Note: 'License-File' warnings are false positives from twine 6.x")
            print("PyPI will accept these packages without issues")
        else:
            print("Package check passed")
    except Exception as e:
        print(f"Warning: Failed to run twine check: {e}")


def clean_bundled_cli() -> None:
    """Clean bundled CLI."""
    bundled_dir = Path("src/claude_agent_sdk/_bundled")
    cli_files = list(bundled_dir.glob("claude*"))

    if cli_files:
        print(f"\n{'=' * 60}")
        print("Cleaning bundled CLI")
        print(f"{'=' * 60}")
        for cli_file in cli_files:
            if cli_file.name != ".gitignore":
                cli_file.unlink()
                print(f"Removed {cli_file}")
    else:
        print("\nNo bundled CLI to clean")


def list_artifacts() -> None:
    """List built artifacts."""
    dist_dir = Path("dist")
    if not dist_dir.exists():
        return

    print(f"\n{'=' * 60}")
    print("Built Artifacts")
    print(f"{'=' * 60}")

    artifacts = sorted(dist_dir.iterdir())
    if not artifacts:
        print("No artifacts found")
        return

    for artifact in artifacts:
        size_mb = artifact.stat().st_size / (1024 * 1024)
        print(f"  {artifact.name:<50} {size_mb:>8.2f} MB")

    total_size = sum(f.stat().st_size for f in artifacts) / (1024 * 1024)
    print(f"\n  {'Total:':<50} {total_size:>8.2f} MB")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build wheel with bundled Claude Code CLI"
    )
    parser.add_argument(
        "--version",
        help="Version to set before building (e.g., 0.1.4)",
    )
    parser.add_argument(
        "--cli-version",
        default=None,
        help="Claude Code CLI version to download (default: read from _cli_version.py)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading CLI (use existing bundled CLI)",
    )
    parser.add_argument(
        "--skip-sdist",
        action="store_true",
        help="Skip building source distribution",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean bundled CLI after building",
    )
    parser.add_argument(
        "--clean-dist",
        action="store_true",
        help="Clean dist directory before building",
    )
    parser.add_argument(
        "--recompress",
        choices=RECOMPRESS_ENCODERS,
        default=None,
        help="Compress the files in the built wheel again so the wheel is smaller; "
        "the files themselves do not change. zlib: quick. zopfli: smaller, takes "
        "minutes, needs the zopfli package (see scripts/requirements-recompress.txt)",
    )

    args = parser.parse_args()
    if args.recompress:
        require_encoder(args.recompress)

    print("\n" + "=" * 60)
    print("Claude Agent SDK - Wheel Builder")
    print("=" * 60)

    # Clean dist if requested
    if args.clean_dist:
        clean_dist()

    # Update version if specified
    if args.version:
        update_version(args.version)

    # Download CLI unless skipped
    if not args.skip_download:
        download_cli(args.cli_version)
    else:
        print("\nSkipping CLI download (using existing)")

    # Build wheel
    build_wheel(args.recompress)

    # Build sdist unless skipped
    if not args.skip_sdist:
        build_sdist()

    # Check package
    check_package()

    # Clean bundled CLI if requested
    if args.clean:
        clean_bundled_cli()

    # List artifacts
    list_artifacts()

    print(f"\n{'=' * 60}")
    print("Build complete!")
    print(f"{'=' * 60}")
    print("\nNext steps:")
    print("  1. Test the wheel: pip install dist/*.whl")
    print("  2. Run tests: python -m pytest tests/")
    print("  3. Publish: twine upload dist/*")


if __name__ == "__main__":
    main()
