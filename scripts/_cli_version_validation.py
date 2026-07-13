r"""Shared validation for Claude Code CLI version strings.

Two scripts constrain the same value: update_cli_version.py writes it into
src/claude_agent_sdk/_cli_version.py, and download_cli.py (reached from
build_wheel.py) reads it back out and hands it to an installer. A second copy
of the rule would let the writer emit a value the reader rejects, so the
pattern and its validation helper live here once.

The installer is the authority on what a version may be. install.sh enforces

    ^(stable|latest|[0-9]+\.[0-9]+\.[0-9]+(-[^[:space:]]+)?)$

and install.ps1 enforces the same rule, so a value this module admits but the
installer does not is not a version -- it is an error we defer to install time,
where it surfaces behind a retry loop and a misleading "Error downloading CLI"
headline. VERSION_PATTERN therefore mirrors that grammar: three dot-separated
numeric components with an optional prerelease/build suffix, which covers both
releases ("2.1.207") and dev builds
("2.1.146-dev.20260519.t105443.shaece3dab").

We deliberately accept a strict *subset* of what the installer allows: the
installer's suffix is `-[^\s]+`, which would admit quotes, backslashes,
semicolons and every other non-space character, so the suffix here is narrowed
to the alphanumeric/dot/plus/hyphen set that real versions use. Never widen
this pattern back toward the installer's.

That narrowing is a security boundary, not just input hygiene:

  * update_cli_version.update_cli_version() writes the version into a Python
    string literal in a real source file, so it must never admit a double
    quote, a backslash, or a newline.
  * download_cli.download_cli() hands the version to an installer. Neither of
    its paths interpolates it into a command string -- Unix passes it as its
    own argv element, Windows passes it in the environment -- so for that
    caller the allowlist is defense in depth rather than the only barrier.

"latest" and "stable" are the installer's dist-tags. Both are *moving*: they
resolve to whatever build is current at install time. That is fine for a
download, and wrong for a pin -- _cli_version.py is the only record of which
build went into the wheels, so it must name one concrete build. Hence
``allow_dist_tag``.

Widening any of this requires re-reading tests/test_download_cli.py and
tests/test_update_cli_version.py.

VERSION_PATTERN is deliberately unanchored, and matched with fullmatch()
rather than match(): with "^...$" a swap to match() would silently accept a
trailing newline ("1.0.0\n"); unanchored, the same swap accepts obvious
prefixes like "1.0.0; id" and fails immediately in tests.
"""

import re

# A concrete version: MAJOR.MINOR.PATCH with an optional suffix. The suffix is
# the installer's `-[^\s]+` narrowed to characters that appear in real
# versions -- see the module docstring.
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.+-]+)?")

# The moving tags the installer resolves at install time. Compared lowercased,
# so "LATEST" is the sentinel rather than a mysterious "concrete version".
DIST_TAGS = ("latest", "stable")

# Anything word-shaped that is not a version: "next", "beta", "nightly". Named
# so the error can say *why* it was rejected instead of printing a regex.
_DIST_TAG_SHAPED = re.compile(r"[A-Za-z][0-9A-Za-z-]*")

_SUPPORTED_TAGS = ", ".join(repr(tag) for tag in DIST_TAGS)


def validate_version(version: str, *, source: str, allow_dist_tag: bool) -> str:
    """Return the usable form of ``version``, or raise.

    Surrounding whitespace is stripped before anything else: a trailing "\\n"
    from a file read, a "\\r" from a CRLF checkout, or a stray space from YAML
    is unambiguous in intent, and the stripped value is what the caller gets
    back and must use downstream.

    Args:
        version: The candidate version string.
        source: Name of where the value came from, used in the error message
            (e.g. "CLAUDE_CLI_VERSION").
        allow_dist_tag: Whether a moving dist-tag ("latest", "stable") is
            acceptable. It is for a download, which resolves it at install
            time; it is not for a value pinned into _cli_version.py, which must
            name the one concrete build that went into the wheels.

    Returns:
        The stripped version, with a dist-tag normalized to lowercase.

    Raises:
        ValueError: If ``version`` is neither an allowed dist-tag nor a
            fullmatch of VERSION_PATTERN.
    """
    candidate = version.strip()

    # A dist-tag fails VERSION_PATTERN, so it is recognized by name -- and
    # case-insensitively, so "LATEST" is not mistaken for something else.
    if candidate.lower() in DIST_TAGS:
        if allow_dist_tag:
            return candidate.lower()
        raise ValueError(
            f"Invalid {source}: {candidate!r} is a moving dist-tag, not a concrete "
            f"version. A pinned version must name the one build that goes into the "
            f"wheels. Expected a version matching {VERSION_PATTERN.pattern}"
        )

    # "v2.1.207" is the single most likely typo, and the installer rejects it.
    # Say so, rather than printing the pattern and leaving the reader to spot
    # the leading "v". Not normalized away: the caller asked for something we
    # do not support, and silently installing a different string is worse.
    if candidate[:1] in ("v", "V") and VERSION_PATTERN.fullmatch(candidate[1:]):
        raise ValueError(
            f"Invalid {source}: {candidate!r}. "
            f"Did you mean {candidate[1:]!r}? (no leading 'v')"
        )

    if _DIST_TAG_SHAPED.fullmatch(candidate):
        expected = (
            f"{_SUPPORTED_TAGS}, or a concrete version"
            if allow_dist_tag
            else "a concrete version"
        )
        raise ValueError(
            f"Invalid {source}: {candidate!r} is not a supported dist-tag; "
            f"use {expected}"
        )

    if not VERSION_PATTERN.fullmatch(candidate):
        expected = (
            f"{_SUPPORTED_TAGS}, or a version"
            if allow_dist_tag
            else "a concrete version"
        )
        raise ValueError(
            f"Invalid {source}: {version!r}. "
            f"Expected {expected} matching {VERSION_PATTERN.pattern}"
        )
    return candidate
