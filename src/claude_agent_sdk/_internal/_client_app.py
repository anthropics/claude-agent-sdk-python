"""Works out an app label for the Claude Code subprocess.

The derived value travels apart from ``CLAUDE_AGENT_SDK_CLIENT_APP`` because
auth is decided in the subprocess: the CLI sends it for a subscription login
and otherwise drops it, so an API customer's package name never leaves the
machine. CLIs that predate the variable ignore it.
"""

import functools
import importlib.metadata
import logging
import re
import sys

logger = logging.getLogger(__name__)

EXPLICIT_CLIENT_APP_ENV = "CLAUDE_AGENT_SDK_CLIENT_APP"
DERIVED_CLIENT_APP_ENV = "CLAUDE_AGENT_SDK_DERIVED_CLIENT_APP"

# Names a scaffold or an entry file leaves behind; they say nothing of the app.
_GENERIC_NAMES = frozenset({"index", "main", "cli", "server", "app", "node"})

# Copied from the CLI, which checks the value again: change them together. A
# package name, scoped or not, and an optional version. Narrower than printable
# ASCII: a space, comma or parenthesis would break the User-Agent suffix the
# label rides in, and a leading "/" or "." would be a path.
_MAX_CLIENT_APP_LENGTH = 128
_CLIENT_APP_PATTERN = re.compile(
    r"(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:/[A-Za-z0-9][A-Za-z0-9.!+_-]*)?"
)

_AGENT_SDK_REQUIREMENT = re.compile(r"claude[-_.]agent[-_.]sdk(?![A-Za-z0-9._-])", re.I)
_EXTRA_MARKER = re.compile(r";.*\bextra\s*==")


def _normalize(value: str) -> str | None:
    """Return the value when it is a sendable label, else None.

    Never a repaired copy, which would name something else.
    """
    if len(value) <= _MAX_CLIENT_APP_LENGTH and _CLIENT_APP_PATTERN.fullmatch(value):
        return value
    return None


def _format_client_app(name: str, version: str | None) -> str | None:
    if name.lower() in _GENERIC_NAMES:
        return None
    label = _normalize(f"{name}/{version}") if version else None
    return label or _normalize(name)


def _requires_agent_sdk(distribution: str) -> bool:
    """Tell an app from a tool that merely launched it (uvicorn, pytest)."""
    requirements = importlib.metadata.requires(distribution) or []
    return any(
        _AGENT_SDK_REQUIREMENT.match(item) and not _EXTRA_MARKER.search(item)
        for item in requirements
    )


@functools.cache
def derive_client_app() -> str | None:
    """Name the running app from its installed distribution.

    Returns None when nothing reliable names it: a plain script, a package with
    no installed distribution, or a generic name. Never raises, and never
    returns a path. Cached because ``packages_distributions()`` reads the
    metadata of every installed distribution; call it off the event loop.
    """
    try:
        spec = getattr(sys.modules.get("__main__"), "__spec__", None)
        # "__main__" is a directory or zipapp run, not a package to look up.
        if spec is None or spec.name == "__main__":
            return None
        top_level_package = spec.name.partition(".")[0]
        distributions = importlib.metadata.packages_distributions().get(
            top_level_package, []
        )
        for distribution in distributions:
            if _requires_agent_sdk(distribution):
                return _format_client_app(
                    distribution, importlib.metadata.version(distribution)
                )
    except Exception:  # noqa: BLE001 - a label must never break connect()
        logger.debug("Could not derive an app label", exc_info=True)
    return None
