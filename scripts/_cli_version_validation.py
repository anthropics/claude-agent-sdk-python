r"""Shared validation for Claude Code CLI version strings.

Two scripts constrain the same value: update_cli_version.py writes it into
src/claude_agent_sdk/_cli_version.py, and download_cli.py (reached from
build_wheel.py) reads it back out and hands it to an installer. A second copy
of the rule would let the writer emit a value the reader rejects, so the
pattern and its validation helper live here once.

A version must start with an alphanumeric character (so flag-shaped values
like "--help" are rejected) and may then contain only characters that appear
in real CLI versions, including dev builds such as
"2.1.146-dev.20260519.t105443.shaece3dab".

This allowlist is a security boundary, not just input hygiene:

  * update_cli_version.update_cli_version() writes the version into a Python
    string literal in a real source file, so it must never admit a double
    quote, a backslash, or a newline.
  * download_cli.download_cli() hands the version to an installer. Neither of
    its paths interpolates it into a command string -- Unix passes it as its
    own argv element, Windows passes it in the environment -- so for that
    caller the allowlist is defense in depth rather than the only barrier.

Widening it requires re-reading tests/test_download_cli.py::TestGetCliVersion
and tests/test_update_cli_version.py.

Deliberately unanchored, and matched with fullmatch() rather than match():
with "^...$" a swap to match() would silently accept a trailing newline
("1.0.0\n"); unanchored, the same swap accepts obvious prefixes like
"1.0.0; id" and fails immediately in tests.
"""

import re

VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]*")


def validate_version(version: str, *, source: str, allow_latest: bool) -> str:
    """Return ``version`` unchanged if it is a usable CLI version.

    Args:
        version: The candidate version string.
        source: Name of where the value came from, used in the error message
            (e.g. "CLAUDE_CLI_VERSION").
        allow_latest: Whether the "latest" sentinel is acceptable. It is for a
            download, which resolves it at install time; it is not for a value
            pinned into _cli_version.py, which must name one concrete build.

    Raises:
        ValueError: If ``version`` is neither an allowed "latest" nor a
            fullmatch of VERSION_PATTERN.
    """
    # "latest" fullmatches VERSION_PATTERN, so it has to be ruled out by name
    # rather than by the pattern when it is not allowed.
    if version == "latest":
        if allow_latest:
            return version
        raise ValueError(
            f"Invalid {source}: 'latest' is not a concrete version. "
            f"Expected a version matching {VERSION_PATTERN.pattern}"
        )

    if not VERSION_PATTERN.fullmatch(version):
        expected = "'latest' or a version" if allow_latest else "a concrete version"
        raise ValueError(
            f"Invalid {source}: {version!r}. "
            f"Expected {expected} matching {VERSION_PATTERN.pattern}"
        )
    return version
