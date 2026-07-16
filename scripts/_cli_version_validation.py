r"""Shared validation for Claude Code CLI version strings.

update_cli_version.py writes the version into src/claude_agent_sdk/_cli_version.py
and download_cli.py reads it back out and hands it to an installer, so the rule
lives here once rather than in two copies that could drift apart.

The installer is the authority on what a version may be. install.sh (and
install.ps1) enforce

    ^(stable|latest|[0-9]+\.[0-9]+\.[0-9]+(-[^[:space:]]+)?)$

VERSION_PATTERN admits a deliberate *subset* of that: the installer's `-[^\s]+`
suffix would accept quotes, backslashes and semicolons, so the suffix here is
narrowed to the characters real versions use. Never widen it back toward the
installer's -- update_cli_version.py writes this value into a Python string
literal in a real source file.

"latest" and "stable" are the installer's dist-tags, and they are *moving*:
fine for a download, wrong for a pin, since _cli_version.py is the only record
of which build went into the wheels. Hence ``allow_dist_tag``.

VERSION_PATTERN is deliberately unanchored, and matched with fullmatch(): with
"^...$" a swap to match() would silently accept a trailing newline ("1.0.0\n");
unanchored, the same swap accepts an obvious prefix like "1.0.0; id" and fails
loudly in tests.
"""

import re

VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.+-]+)?")

# Matched exactly: install.sh's grammar is case-sensitive, so "Latest" is not a
# tag it would resolve, and accepting it here would widen what reaches a real
# network install.
DIST_TAGS = ("latest", "stable")

# Anything word-shaped that is not a version: "next", "beta", "nightly". Named
# so the error can say *why* it was rejected instead of printing a regex.
_DIST_TAG_SHAPED = re.compile(r"[A-Za-z][0-9A-Za-z-]*")

_SUPPORTED_TAGS = ", ".join(repr(tag) for tag in DIST_TAGS)


def _expected(allow_dist_tag: bool) -> str:
    """The phrase naming what the caller should have passed instead."""
    if allow_dist_tag:
        return f"{_SUPPORTED_TAGS}, or a concrete version"
    return "a concrete version"


def _rejection(version: str, allow_dist_tag: bool) -> str:
    """Why ``version`` is unusable -- the most specific reason that applies.

    The caller prefixes "Invalid <source>: ", so each reason reads as the rest
    of that sentence.
    """
    candidate = version.strip()

    # Reachable only when a tag is not a legal answer: validate_version()
    # returns the tag otherwise.
    if candidate in DIST_TAGS:
        return (
            f"{candidate!r} is a moving dist-tag, not a concrete version. A pinned "
            f"version must name the one build that goes into the wheels. Expected "
            f"a version matching {VERSION_PATTERN.pattern}"
        )

    # Only where a tag is a legal answer at all; when pinning, the
    # dist-tag-shaped branch below correctly says to use a concrete version.
    if allow_dist_tag and candidate.lower() in DIST_TAGS:
        return f"{candidate!r}. Did you mean {candidate.lower()!r}?"

    # Never normalized away: the caller asked for something we do not support,
    # and silently installing a different string is worse.
    if candidate[:1] in ("v", "V") and VERSION_PATTERN.fullmatch(candidate[1:]):
        return f"{candidate!r}. Did you mean {candidate[1:]!r}? (no leading 'v')"

    if _DIST_TAG_SHAPED.fullmatch(candidate):
        return (
            f"{candidate!r} is not a supported dist-tag; "
            f"use {_expected(allow_dist_tag)}"
        )

    # Name the raw value, not the stripped one: if the whitespace is the
    # problem, the reader has to be able to see it.
    return (
        f"{version!r}. Expected {_expected(allow_dist_tag)} "
        f"matching {VERSION_PATTERN.pattern}"
    )


def validate_version(version: str, *, source: str, allow_dist_tag: bool) -> str:
    """Return the usable form of ``version``, or raise.

    Surrounding whitespace is stripped first -- a trailing "\\n" from a file
    read or a "\\r" from a CRLF checkout is unambiguous in intent -- and the
    stripped value is what the caller gets back and must use downstream.

    Args:
        version: The candidate version string.
        source: Where the value came from, named in the error message
            (e.g. "CLAUDE_CLI_VERSION").
        allow_dist_tag: Whether a moving dist-tag ("latest", "stable") is
            acceptable. It is for a download, which resolves it at install
            time; it is not for a value pinned into _cli_version.py.

    Raises:
        ValueError: If ``version`` is neither an allowed dist-tag nor a
            fullmatch of VERSION_PATTERN.
    """
    candidate = version.strip()

    if candidate in DIST_TAGS and allow_dist_tag:
        return candidate

    if VERSION_PATTERN.fullmatch(candidate):
        return candidate

    raise ValueError(f"Invalid {source}: {_rejection(version, allow_dist_tag)}")
