"""Regression tests for non-BMP project directory naming (issue #1409).

The CLI derives project directory names in JavaScript, which runs on UTF-16
code units: a character outside the Basic Multilingual Plane is a surrogate
pair — two units for the regex to replace with two hyphens, two charCodeAt
steps for the hash, and two units toward the 200-unit length cap. The SDK used
to iterate Python code points and count such a character once, producing a
different directory name for the same path.
"""

from claude_agent_sdk._internal.sessions import (
    _sanitize_path,
    _simple_hash,
    _utf16_length,
)

# Reference values produced by the CLI's own JS implementation, quoted from
# the issue report:
#   "/Users/foo/\u{1F680} launch".replace(/[^a-zA-Z0-9]/g, "-")
#     === "-Users-foo----launch"
#   simpleHash("\u{1F680}") === "1202r"
CLI_SANITIZE_EMOJI_PATH = "-Users-foo----launch"
CLI_HASH_ROCKET = "1202r"


def test_sanitize_replaces_non_bmp_char_with_two_hyphens():
    assert _sanitize_path("/Users/foo/\U0001f680 launch") == CLI_SANITIZE_EMOJI_PATH


def test_hash_iterates_utf16_code_units():
    assert _simple_hash("\U0001f680") == CLI_HASH_ROCKET


def test_bmp_paths_are_unchanged():
    assert _sanitize_path("/Users/foo/launch") == "-Users-foo-launch"


def test_long_path_uses_utf16_length_for_truncation():
    # 200 BMP code points plus one emoji: 201 UTF-16 code units, over the cap
    # in CLI terms even though it is 201 characters in Python terms too — the
    # cap is on the sanitized string, where the emoji became two hyphens.
    path = "/" + "a" * 200 + "\U0001f680"
    out = _sanitize_path(path)
    assert out.rsplit("-", 1)[-1] == _simple_hash(path), (
        "over-cap paths must carry the CLI-compatible hash suffix"
    )


def test_truncation_never_splits_a_surrogate_pair():
    # A path whose sanitized form is exactly at the cap boundary in UTF-16
    # units: the character straddling the cut must be dropped whole.
    body = "a" * 198 + "\U0001f680" + "bcdef"
    out = _sanitize_path("/" + body)
    assert _utf16_length(out) <= 200 + 1 + len(_simple_hash("/" + body))
