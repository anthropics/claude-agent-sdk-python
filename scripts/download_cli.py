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
from typing import NamedTuple, NoReturn

# scripts/ is not a package. Running this file directly puts scripts/ on
# sys.path, but loading it by path (as the tests do) does not. Appended, not
# prepended: the tests' entry outlives the import, and a prepended scripts/
# could shadow a stdlib module for the whole pytest process.
_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.append(_SCRIPTS_DIR)

import _cli_version_validation as version_validation  # noqa: E402

# Re-exported: this module's callers and tests refer to download_cli.VERSION_PATTERN.
VERSION_PATTERN = version_validation.VERSION_PATTERN

# The Windows installer reads the version and the downloaded script's path out
# of the environment under these names, so neither is part of the command text
# PowerShell parses. `$env:NAME` in argument position expands to exactly one
# argument -- the same argv separation the Unix path gets from
# `["bash", script, version]`.
INSTALL_VERSION_ENV_VAR = "CLAUDE_CLI_INSTALL_VERSION"
INSTALL_SCRIPT_ENV_VAR = "CLAUDE_CLI_INSTALL_SCRIPT"

# How many times retry_install() runs an attempt before giving up.
MAX_INSTALL_ATTEMPTS = 3

# What an unset CLAUDE_CLI_VERSION means, and -- being the installer's own
# default -- the one value both install paths express by passing no argument.
DEFAULT_VERSION = "latest"


def get_cli_version() -> str:
    """Get the CLI version to download from environment or default.

    Returns the stripped value -- use it, rather than the raw environment
    string, for everything downstream.

    Raises:
        ValueError: If CLAUDE_CLI_VERSION is set to something other than a
            dist-tag ("latest", "stable") or a value matching VERSION_PATTERN.
    """
    version = os.environ.get("CLAUDE_CLI_VERSION", DEFAULT_VERSION)
    return version_validation.validate_version(
        version, source="CLAUDE_CLI_VERSION", allow_dist_tag=True
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


def _reject_install_script(script_path: str, reason: str) -> NoReturn:
    """Report a downloaded installer we refuse to execute, and exit."""
    head = Path(script_path).read_bytes()[:64]
    print(
        f"Error: downloaded install script {reason} (first bytes: {head!r}). "
        f"Refusing to execute it.",
        file=sys.stderr,
    )
    sys.exit(1)


def check_install_script(script_path: str) -> None:
    """Reject a downloaded install.sh that is not a shell script.

    claude.ai answers unknown paths with HTTP 200 and an HTML body, which
    `curl -f` cannot detect, so check the shebang before executing the file.
    A wrong body is deterministic, so this fails fast instead of retrying.
    """
    with Path(script_path).open("rb") as f:
        magic = f.read(2)
    if magic != b"#!":
        _reject_install_script(script_path, "does not start with a shebang")


def check_powershell_install_script(script_path: str) -> None:
    """Reject a downloaded install.ps1 that is not a PowerShell script.

    The same HTTP 200 + HTML body is invisible to Invoke-RestMethod. A .ps1 has
    no shebang to check, so reject what the error page actually is -- an
    XML/HTML document, whose first non-blank character is '<' -- and an empty
    body. A wrong body is deterministic, so this fails fast instead of retrying.

    '<' alone would be too blunt: a .ps1 may legitimately open with '<#', the
    comment-based-help block real installers start with. No HTML or XML document
    begins with that, so exempt it.
    """
    body = Path(script_path).read_bytes()
    # A UTF-8 BOM is legal in a .ps1 and common in PowerShell-authored files.
    stripped = body.removeprefix(b"\xef\xbb\xbf").lstrip()
    if not stripped:
        _reject_install_script(script_path, "is empty")
    if stripped.startswith(b"<") and not stripped.startswith(b"<#"):
        _reject_install_script(script_path, "looks like an HTML or XML document")


def _decode(stream: bytes | str | None) -> str:
    """One captured stream as text. run_command() captures bytes; be tolerant."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode(errors="replace")
    return stream


def retry_install(attempt: Callable[[], None]) -> None:
    """Run an install attempt, retrying the whole attempt on command failure."""
    # Small jitter to stagger parallel matrix builds hitting the same endpoint
    time.sleep(random.uniform(0, 5))

    for attempt_num in range(1, MAX_INSTALL_ATTEMPTS + 1):
        try:
            attempt()
            return
        except OSError as e:
            # The command could not be started at all: no `curl`, no `bash`, no
            # `powershell` on PATH. Deterministic -- a second attempt cannot
            # make the binary appear -- so fail immediately rather than sleeping
            # through three of them and then blaming the download.
            print(f"Error: could not run the install command: {e}", file=sys.stderr)
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            if attempt_num == MAX_INSTALL_ATTEMPTS:
                print(
                    f"Error downloading CLI after {MAX_INSTALL_ATTEMPTS} attempts: {e}",
                    file=sys.stderr,
                )
                print(f"stdout: {_decode(e.stdout)}", file=sys.stderr)
                print(f"stderr: {_decode(e.stderr)}", file=sys.stderr)
                sys.exit(1)

            delay = 2**attempt_num
            print(
                f"Install attempt {attempt_num} failed (exit {e.returncode}), "
                f"retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)


# One command to run: its argv, and the environment to run it with (None
# inherits ours).
_Command = tuple[list[str], dict[str, str] | None]


class _InstallPlan(NamedTuple):
    """How one platform downloads the installer, checks its body, and runs it."""

    script_path: str
    download: _Command
    check: Callable[[str], None]
    install: _Command


def _powershell(command: str) -> list[str]:
    """A PowerShell invocation of ``command``."""
    return ["powershell", "-ExecutionPolicy", "Bypass", "-Command", command]


def _windows_plan(tmpdir: str, version: str) -> _InstallPlan:
    script_path = str(Path(tmpdir) / "install.ps1")

    download_env = {**os.environ, INSTALL_SCRIPT_ENV_VAR: script_path}
    download_cmd = _powershell(
        "$ProgressPreference = 'SilentlyContinue'; "
        "Invoke-RestMethod -Uri https://claude.ai/install.ps1 "
        f"-OutFile $env:{INSTALL_SCRIPT_ENV_VAR}"
    )

    install_env = dict(download_env)
    install_command = f"& $env:{INSTALL_SCRIPT_ENV_VAR}"
    if version != DEFAULT_VERSION:
        install_command += f" $env:{INSTALL_VERSION_ENV_VAR}"
        install_env[INSTALL_VERSION_ENV_VAR] = version

    return _InstallPlan(
        script_path=script_path,
        download=(download_cmd, download_env),
        check=check_powershell_install_script,
        install=(_powershell(install_command), install_env),
    )


def _unix_plan(tmpdir: str, version: str) -> _InstallPlan:
    script_path = str(Path(tmpdir) / "install.sh")

    # -L follows the cross-host redirect to the bootstrap script.
    # --retry-all-errors covers 429 from claude.ai when multiple matrix jobs
    # fetch install.sh simultaneously.
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
    if version != DEFAULT_VERSION:
        bash_cmd.append(version)

    return _InstallPlan(
        script_path=script_path,
        download=(curl_cmd, None),
        check=check_install_script,
        install=(bash_cmd, None),
    )


def download_cli() -> None:
    """Download Claude Code CLI using the official install script.

    Both platforms download the installer to a temp file, check its body, and
    only then execute it -- rather than piping the response straight into a
    shell or into `iex`. The whole sequence is retried, so a truncated body is
    never reused across attempts.

    Both pass "latest" by passing no argument at all: it is the installer's own
    default. Every other accepted value -- a concrete version, or "stable" --
    goes through the argument path.
    """
    version = get_cli_version()
    print(f"Downloading Claude Code CLI version: {version}")

    build_plan = _windows_plan if platform.system() == "Windows" else _unix_plan

    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_plan(tmpdir, version)

        def attempt() -> None:
            run_command(*plan.download)
            plan.check(plan.script_path)
            run_command(*plan.install)

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
    # This script runs as a build step, so report a bad CLAUDE_CLI_VERSION the
    # way every other failure here is reported -- one line on stderr, exit 1 --
    # instead of letting a traceback out. The shared validator keeps raising:
    # only the entry point turns it into an exit status.
    try:
        main()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
