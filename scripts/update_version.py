#!/usr/bin/env python3
r"""Update the SDK version in pyproject.toml and src/claude_agent_sdk/_version.py.

This script is the release flow's writer: the version it is handed (ultimately
from a workflow_dispatch input, see .github/workflows/build-and-publish.yml)
is written verbatim into two source files as a *string literal* -- a TOML one
in pyproject.toml and a Python one in _version.py. That makes the value code,
not data, so it is validated against an allowlist before it is used anywhere,
and substituted with a callable replacement so it can never be reinterpreted.

Two distinct hazards, both real, both closed here:

  1. **Quote breakout.** An unvalidated value written as ``version = "{v}"``
     escapes its literal the moment it contains a double quote: a ``v`` of
     ``0.0.0"\nimport os; os.system("...")\n#`` turns _version.py into a
     source file that executes on import. VERSION_PATTERN admits no quote, no
     backslash, and no newline; json.dumps() then emits the literal, so the
     quoting is done by a serializer rather than by an f-string.

  2. **Backslash expansion in the replacement.** ``re.sub(pat, repl, s)`` with
     a *string* ``repl`` does not insert it literally -- it runs it through
     escape processing first, so a ``\1`` or ``\g<0>`` in the version is
     expanded against the match, and a lone ``\`` raises. The replacements
     below are therefore **callables**: re.sub() inserts a callable's return
     value literally, with no escape pass. This is belt-and-braces given the
     pattern already excludes backslashes, and it is the property the tests
     pin.

The allowlist is deliberately narrow. Every version this project has ever
released -- all 121 git tags, and all 121 releases on PyPI -- is a plain
``MAJOR.MINOR.PATCH`` with no suffix. The pattern additionally admits the
PEP 440 pre-release / post-release / dev-release suffixes that PyPI would
accept for a *future* ``0.3.0rc1``-style release, so release day is not the
day someone discovers this script rejects a legitimate version. Every
character it can admit is drawn from ``[0-9a-z.]``: there is no metacharacter,
quote, backslash, or space in the admissible set, which is what makes both
hazards above unreachable rather than merely unlikely.

Note: scripts/_cli_version_validation.py (added by #1117) validates the
bundled *CLI* version and enforces the same two properties for the same
reasons. The two allowlists differ -- a CLI version has a ``-dev.…`` suffix
and dist-tags, an SDK version has PEP 440 suffixes and must satisfy PyPI --
but the validate-then-substitute-with-a-callable shape is common to both, and
they should be unified behind one shared validator once #1117 lands.
"""

import json
import re
import sys
from pathlib import Path

# MAJOR.MINOR.PATCH, plus the optional PEP 440 suffixes PyPI would accept.
# Admissible characters are exactly [0-9a-z.] -- see the module docstring for
# why that, and not the numeric arity, is the security boundary. Never widen
# this to admit a quote, a backslash, whitespace, or a shell metacharacter.
#
# Deliberately unanchored, and matched with fullmatch() rather than match():
# with "^...$" a later swap to match() would silently accept a trailing
# newline ("0.2.119\n"); unanchored, the same swap accepts an obvious prefix
# like "0.2.119; id" and fails loudly in the tests.
VERSION_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+"  # 0.2.119 -- every version ever released
    r"(?:(?:a|b|rc)[0-9]+)?"  # 0.3.0rc1  (PEP 440 pre-release)
    r"(?:\.post[0-9]+)?"  # 0.3.0.post1 (PEP 440 post-release)
    r"(?:\.dev[0-9]+)?"  # 0.3.0.dev1  (PEP 440 dev release)
)


def validate_version(version: str, *, source: str = "version") -> str:
    """Return ``version`` if it is a version this project could publish, else raise.

    Surrounding whitespace is stripped first: a trailing "\\n" from a file read
    or a stray space from YAML is unambiguous in intent. Interior whitespace is
    not stripped and is rejected by the pattern.

    Args:
        version: The candidate version string.
        source: Where the value came from, used in the error message.

    Returns:
        The stripped, validated version.

    Raises:
        ValueError: If ``version`` is not a fullmatch of VERSION_PATTERN.
    """
    candidate = version.strip()

    if VERSION_PATTERN.fullmatch(candidate):
        return candidate

    # "v0.2.119" is the single most likely typo -- the tags carry a leading
    # "v" but the version does not. Say so, rather than printing a regex and
    # leaving the reader to spot it. Not normalized away: publishing a
    # different string than the caller asked for is worse than failing.
    if candidate[:1] in ("v", "V") and VERSION_PATTERN.fullmatch(candidate[1:]):
        raise ValueError(
            f"Invalid {source}: {version!r}. "
            f"Did you mean {candidate[1:]!r}? (no leading 'v')"
        )

    raise ValueError(
        f"Invalid {source}: {version!r}. "
        f"Expected a version matching {VERSION_PATTERN.pattern}"
    )


def _substitute(path: Path, pattern: str, assignment: str, version: str) -> str:
    """Return ``path``'s text with ``assignment``'s version literal set to ``version``.

    The replacement is a **callable**, so re.sub() inserts its return value
    literally instead of running it through backslash-escape processing -- a
    "\\1" or "\\g<0>" in ``version`` stays those four characters. json.dumps()
    emits the quoted literal, which is valid in both TOML and Python.

    Raises:
        ValueError: If ``pattern`` does not match, which would otherwise write
            the file back unchanged and report success.
    """
    content = path.read_text()
    literal = f"{assignment} = {json.dumps(version)}"
    new_content, count = re.subn(
        pattern,
        lambda _match: literal,
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Could not find a {assignment!r} assignment in {path}")
    return new_content


def update_version(new_version: str) -> None:
    """Validate ``new_version`` and write it into pyproject.toml and _version.py."""
    version = validate_version(new_version)

    pyproject_path = Path("pyproject.toml")
    version_path = Path("src/claude_agent_sdk/_version.py")

    # Compute both substitutions before writing either, so a missing anchor in
    # the second file cannot leave the first one already rewritten.
    pyproject_content = _substitute(
        pyproject_path, r'^version = "[^"]*"', "version", version
    )
    version_content = _substitute(
        version_path, r'^__version__ = "[^"]*"', "__version__", version
    )

    pyproject_path.write_text(pyproject_content)
    print(f"Updated pyproject.toml to version {version}")

    version_path.write_text(version_content)
    print(f"Updated _version.py to version {version}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/update_version.py <version>")

    try:
        update_version(sys.argv[1])
    except ValueError as exc:
        # A clean one-line message, not a traceback: the caller is a release
        # workflow and the reader is whoever typed the bad version.
        sys.exit(str(exc))
