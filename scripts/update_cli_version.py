#!/usr/bin/env python3
"""Update Claude Code CLI version in _cli_version.py."""

import json
import re
import sys
from pathlib import Path

# scripts/ is not a package; see the note in download_cli.py.
_SCRIPTS_DIR = str(Path(__file__).parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.append(_SCRIPTS_DIR)

import _cli_version_validation as version_validation  # noqa: E402

DEFAULT_VERSION_PATH = Path("src/claude_agent_sdk/_cli_version.py")

# Matches the assignment build_wheel.get_bundled_cli_version() reads back.
ASSIGNMENT_PATTERN = re.compile(r'__cli_version__ = "[^"]+"')


def update_cli_version(new_version: str, version_path: Path | None = None) -> None:
    """Update CLI version in _cli_version.py.

    Raises:
        ValueError: If ``new_version`` is not a concrete version, or if the
            target file has no ``__cli_version__`` assignment to replace. The
            file is left untouched in both cases.
    """
    # Validate before touching the file: this writes into a real source file
    # that later gets imported, so an unvalidated value closes the string
    # literal and injects arbitrary Python. The validated value is the input
    # with surrounding whitespace stripped -- write that, not the raw input.
    new_version = version_validation.validate_version(
        new_version, source="CLI version", allow_dist_tag=False
    )

    if version_path is None:
        version_path = DEFAULT_VERSION_PATH
    content = version_path.read_text()

    # json.dumps() rather than an f-string: it always emits a closed,
    # double-quoted, fully escaped literal, so a widened VERSION_PATTERN could
    # never make this file unparseable or inject code. repr() is not an option
    # -- it emits single quotes, which build_wheel.py's reader would not match.
    literal = f"__cli_version__ = {json.dumps(new_version)}"

    # A callable replacement, because re.sub() applies backslash-escape
    # processing to a *string* replacement -- \1 and \g<0> expand, \n becomes a
    # newline, and a bare trailing \ raises. A callable's return value is used
    # literally.
    new_content, count = ASSIGNMENT_PATTERN.subn(
        lambda _match: literal, content, count=1
    )
    if count != 1:
        raise ValueError(f"No __cli_version__ assignment found in {version_path}")

    version_path.write_text(new_content)
    print(f"Updated {version_path} to {new_version}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/update_cli_version.py <version>", file=sys.stderr)
        sys.exit(1)

    try:
        update_cli_version(sys.argv[1])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
