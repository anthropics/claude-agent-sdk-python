#!/usr/bin/env python3
"""Update version in pyproject.toml and __init__.py files."""

import argparse
import re
import sys
from pathlib import Path


def get_current_version() -> str:
    """Get current version from pyproject.toml."""
    pyproject_path = Path("pyproject.toml")
    content = pyproject_path.read_text()
    match = re.search(r'^version = "([^"]+)"', content, re.MULTILINE)
    if match:
        return match.group(1)
    raise ValueError("Could not find version in pyproject.toml")


def increment_patch(version: str) -> str:
    """Increment the patch version."""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {version}")
    major, minor, patch = parts
    return f"{major}.{minor}.{int(patch) + 1}"


def update_version(new_version: str) -> None:
    """Update version in project files."""
    # Update pyproject.toml
    pyproject_path = Path("pyproject.toml")
    content = pyproject_path.read_text()

    # Only update the version field in [project] section
    content = re.sub(
        r'^version = "[^"]*"',
        f'version = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    pyproject_path.write_text(content)
    print(f"Updated pyproject.toml to version {new_version}")

    # Update _version.py
    version_path = Path("src/claude_agent_sdk/_version.py")
    content = version_path.read_text()

    # Only update __version__ assignment
    content = re.sub(
        r'^__version__ = "[^"]*"',
        f'__version__ = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    version_path.write_text(content)
    print(f"Updated _version.py to version {new_version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update package version")
    parser.add_argument("version", nargs="?", help="New version to set")
    parser.add_argument("--get", action="store_true", help="Print current version")
    parser.add_argument(
        "--patch", action="store_true", help="Auto-increment patch version"
    )

    args = parser.parse_args()

    if args.get:
        print(get_current_version())
    elif args.patch:
        current = get_current_version()
        new_ver = increment_patch(current)
        update_version(new_ver)
    elif args.version:
        update_version(args.version)
    else:
        parser.print_help()
        sys.exit(1)
