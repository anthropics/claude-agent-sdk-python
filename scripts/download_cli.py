#!/usr/bin/env python3
"""Download Claude Code CLI binary for bundling in wheel.

This script is run during the wheel build process to fetch the Claude Code CLI
binary using the official install script and place it in the package directory.
"""

import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

# scripts/ is not a package. Running this file directly -- as build_wheel.py
# does, via `python scripts/download_cli.py` -- already puts scripts/ on
# sys.path, but loading it by path (importlib.spec_from_file_location, as the
# tests do) does not. Add it either way so the shared module resolves.
_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import _cli_version_validation as version_validation  # noqa: E402

# Re-exported: this module's callers and tests refer to download_cli.VERSION_PATTERN.
VERSION_PATTERN = version_validation.VERSION_PATTERN

# The Windows installer reads the version out of the environment under this
# name instead of having it spliced into the PowerShell command text.
INSTALL_VERSION_ENV_VAR = "CLAUDE_CLI_INSTALL_VERSION"


def get_cli_version() -> str:
    """Get the CLI version to download from environment or default.

    Raises:
        ValueError: If CLAUDE_CLI_VERSION is set to something other than
            "latest" or a value matching VERSION_PATTERN.
    """
    version = os.environ.get("CLAUDE_CLI_VERSION", "latest")
    return version_validation.validate_version(
        version, source="CLAUDE_CLI_VERSION", allow_latest=True
    )


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


def run_command(command: list[str], env: dict[str, str] | None = None) -> None:
    """Run one install command with no shell and no inherited stdin.

    install.sh runs `claude install`, which branches on `[ -t 0 ]`. Under the
    old `curl | bash` its stdin was the pipe, so it never saw a TTY; keep it
    that way rather than handing it the caller's terminal.

    ``env`` replaces the child's environment when given; None inherits ours.
    """
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def check_install_script(script_path: str) -> None:
    """Reject a downloaded install script that is not a shell script.

    claude.ai answers unknown paths with HTTP 200 and an HTML body, which
    `curl -f` cannot detect, so check the shebang before executing the file.
    A wrong body is deterministic, so this fails fast instead of retrying.
    """
    with Path(script_path).open("rb") as f:
        magic = f.read(2)
    if magic != b"#!":
        print(
            f"Error: downloaded install script does not start with a shebang "
            f"(first bytes: {magic!r}). Refusing to execute it.",
            file=sys.stderr,
        )
        sys.exit(1)


def retry_install(attempt: Callable[[], None]) -> None:
    """Run an install attempt, retrying the whole attempt on command failure."""
    # Small jitter to stagger parallel matrix builds hitting the same endpoint
    time.sleep(random.uniform(0, 5))

    last_err: subprocess.CalledProcessError | None = None
    for attempt_num in range(1, 4):
        try:
            attempt()
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            if attempt_num < 3:
                delay = 2**attempt_num
                print(
                    f"Install attempt {attempt_num} failed (exit {e.returncode}), "
                    f"retrying in {delay}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)

    print(f"Error downloading CLI after 3 attempts: {last_err}", file=sys.stderr)
    print(f"stdout: {last_err.stdout.decode()}", file=sys.stderr)
    print(f"stderr: {last_err.stderr.decode()}", file=sys.stderr)
    sys.exit(1)


def download_cli() -> None:
    """Download Claude Code CLI using the official install script."""
    version = get_cli_version()
    system = platform.system()

    print(f"Downloading Claude Code CLI version: {version}")

    # Build install command based on platform
    if system == "Windows":
        # Use PowerShell installer on Windows. The version is handed to
        # PowerShell in the environment and referenced by name, so it is never
        # part of the command text that PowerShell parses. `$env:NAME` in
        # argument position expands to exactly one argument -- PowerShell does
        # not re-split or re-parse it -- which is the argv separation the Unix
        # path gets from `["bash", script, version]`.
        install_env: dict[str, str] | None = None
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
                "& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) "
                f"$env:{INSTALL_VERSION_ENV_VAR}",
            ]
            install_env = {**os.environ, INSTALL_VERSION_ENV_VAR: version}
        retry_install(lambda: run_command(install_cmd, env=install_env))
        return

    # Download install.sh to a file and run it directly rather than piping
    # curl into bash through a shell string: nothing is interpolated into a
    # command line, and check=True sees curl's and bash's exit codes
    # separately instead of only the last status of a pipeline.
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = str(Path(tmpdir) / "install.sh")

        # -L follows the cross-host redirect to the bootstrap script.
        # --retry-all-errors covers 429 from claude.ai when multiple matrix
        # jobs fetch install.sh simultaneously.
        curl_cmd = [
            "curl",
            "-fsSL",
            "--retry",
            "5",
            "--retry-delay",
            "2",
            "--retry-all-errors",
            "-o",
            script_path,
            "https://claude.ai/install.sh",
        ]
        bash_cmd = ["bash", script_path]
        if version != "latest":
            bash_cmd.append(version)

        def attempt() -> None:
            run_command(curl_cmd)
            check_install_script(script_path)
            run_command(bash_cmd)

        retry_install(attempt)


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
