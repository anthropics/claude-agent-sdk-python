#!/usr/bin/env python3
"""Download Claude Code CLI binary for bundling in wheel.

This script is run during the wheel build process to fetch the Claude Code CLI
binary using the official install script and place it in the package directory.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_cli_version() -> str:
    """Get the CLI version to download from environment or default."""
    return os.environ.get("CLAUDE_CLI_VERSION", "latest")


def find_installed_cli() -> Path | None:
    """Find the installed Claude CLI binary."""
    # Check common installation locations
    locations = [
        Path.home() / ".local/bin/claude",
        Path("/usr/local/bin/claude"),
        Path.home() / "node_modules/.bin/claude",
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

    print(f"Downloading Claude Code CLI version: {version}")

    # Download and run install script
    install_script = "curl -fsSL https://claude.ai/install.sh | bash"
    if version != "latest":
        install_script = f"curl -fsSL https://claude.ai/install.sh | bash -s {version}"

    try:
        subprocess.run(
            install_script,
            shell=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error downloading CLI: {e}", file=sys.stderr)
        print(f"stdout: {e.stdout.decode()}", file=sys.stderr)
        print(f"stderr: {e.stderr.decode()}", file=sys.stderr)
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


def main() -> None:
    """Main entry point."""
    print("=" * 60)
    print("Claude Code CLI Download Script")
    print("=" * 60)

    # Download CLI
    download_cli()

    # Copy to bundle directory
    copy_cli_to_bundle()

    print("=" * 60)
    print("CLI download and bundling complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
