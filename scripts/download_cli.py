#!/usr/bin/env python3
"""Download Claude Code CLI binary for bundling in wheel.

This script is run during the wheel build process to fetch the Claude Code CLI
binary using the official install script and place it in the package directory.
"""

import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def get_cli_version() -> str:
    """Get the CLI version to download from environment or default."""
    return os.environ.get("CLAUDE_CLI_VERSION", "latest")


def find_installed_cli() -> Path | None:
    """Find the installed Claude CLI binary."""
    system = platform.system()

    if system == "Windows":
        # Windows installation locations (matches test.yml: $USERPROFILE\.local\bin)
        locations = [
            Path.home() / ".local" / "bin" / "claude.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Claude" / "claude.exe",
        ]
    else:
        # Unix installation locations
        locations = [
            Path.home() / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path.home() / "node_modules" / ".bin" / "claude",
        ]

    # Also check PATH
    cli_path = shutil.which("claude")
    if cli_path:
        return Path(cli_path)

    for path in locations:
        if path.exists() and path.is_file():
            return path

    return None


def download_cli() -> None:
    """Download Claude Code CLI using the official install script."""
    version = get_cli_version()
    system = platform.system()

    print(f"Downloading Claude Code CLI version: {version}")

    # Build install command based on platform
    if system == "Windows":
        # Use PowerShell installer on Windows
        if version == "latest":
            install_cmd = [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "irm https://claude.ai/install.ps1 | iex",
            ]
        else:
            install_cmd = [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) {version}",
            ]
    else:
        # --retry-all-errors covers 429 from claude.ai when multiple matrix
        # jobs fetch install.sh simultaneously. pipefail propagates curl's
        # exit code through the pipe so subprocess.run's check=True catches it.
        curl = "curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors https://claude.ai/install.sh"
        target = "" if version == "latest" else f" -s {version}"
        install_cmd = ["bash", "-c", f"set -o pipefail; {curl} | bash{target}"]

    # Small jitter to stagger parallel matrix builds hitting the same endpoint
    time.sleep(random.uniform(0, 5))

    last_err: subprocess.CalledProcessError | None = None
    for attempt in range(1, 4):
        try:
            subprocess.run(install_cmd, check=True, capture_output=True)
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            if attempt < 3:
                delay = 2**attempt
                print(
                    f"Install attempt {attempt} failed (exit {e.returncode}), "
                    f"retrying in {delay}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)

    print(f"Error downloading CLI after 3 attempts: {last_err}", file=sys.stderr)
    print(f"stdout: {last_err.stdout.decode()}", file=sys.stderr)
    print(f"stderr: {last_err.stderr.decode()}", file=sys.stderr)
    sys.exit(1)


def copy_cli_to_bundle() -> None:
    """Copy the installed CLI to the package _bundled directory."""
    # Find project root (parent of scripts directory)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    bundle_dir = project_root / "src" / "claude_agent_sdk" / "_bundled"

    # Ensure bundle directory exists
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Find installed CLI
    cli_path = find_installed_cli()
    if not cli_path:
        print("Error: Could not find installed Claude CLI binary", file=sys.stderr)
        sys.exit(1)

    print(f"Found CLI at: {cli_path}")

    # Determine target filename based on platform
    system = platform.system()
    target_name = "claude.exe" if system == "Windows" else "claude"
    target_path = bundle_dir / target_name

    # Copy the binary
    print(f"Copying CLI to: {target_path}")
    shutil.copy2(cli_path, target_path)

    # Make it executable (Unix-like systems)
    if system != "Windows":
        target_path.chmod(0o755)

    print(f"Successfully bundled CLI binary: {target_path}")

    # Print size info
    size_mb = target_path.stat().st_size / (1024 * 1024)
    print(f"Binary size: {size_mb:.2f} MB")


def verify_bundled_cli_version() -> None:
    """Verify the bundled CLI binary's reported version matches the requested
    version, and that it matches `__cli_version__` in `_cli_version.py`.

    Catches drift between the install script (which can silently fall back to
    a different release when an exact version is unavailable) and the version
    constant baked into the wheel. See issue #868.
    """
    requested = get_cli_version()
    if requested == "latest":
        # Nothing to verify against; user explicitly opted out of pinning.
        return

    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    bundle_dir = project_root / "src" / "claude_agent_sdk" / "_bundled"
    target_name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled_path = bundle_dir / target_name

    if not bundled_path.exists():
        print(
            f"Error: Bundled CLI not found at {bundled_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            [str(bundled_path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(
            f"Error: Failed to run bundled CLI to verify version: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Output looks like "2.1.118 (Claude Code)" — grab the leading version token.
    match = re.match(r"\s*(\d+\.\d+\.\d+)", result.stdout)
    if not match:
        print(
            f"Error: Could not parse CLI version from output: {result.stdout!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    actual = match.group(1)
    if actual != requested:
        print(
            f"Error: Bundled CLI version mismatch — requested {requested!r}, "
            f"installed {actual!r}. The install script likely fell back to a "
            f"different release. Aborting build to avoid shipping a wheel "
            f"whose bundled binary disagrees with __cli_version__.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Also cross-check the constant in _cli_version.py so the changelog and
    # the wheel always agree.
    cli_version_file = project_root / "src" / "claude_agent_sdk" / "_cli_version.py"
    if cli_version_file.exists():
        constant_match = re.search(
            r'__cli_version__\s*=\s*"([^"]+)"', cli_version_file.read_text()
        )
        if constant_match and constant_match.group(1) != actual:
            print(
                f"Error: __cli_version__ in _cli_version.py is "
                f"{constant_match.group(1)!r} but bundled binary reports "
                f"{actual!r}. Update _cli_version.py or rebuild against the "
                f"matching CLI release.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Verified bundled CLI version: {actual}")


def main() -> None:
    """Main entry point."""
    print("=" * 60)
    print("Claude Code CLI Download Script")
    print("=" * 60)

    # Download CLI
    download_cli()

    # Copy to bundle directory
    copy_cli_to_bundle()

    # Verify the bundled binary actually matches the requested version.
    verify_bundled_cli_version()

    print("=" * 60)
    print("CLI download and bundling complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
