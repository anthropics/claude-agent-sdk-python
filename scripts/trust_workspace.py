#!/usr/bin/env python3
"""Mark the CI workspace as trusted in Claude Code's global config.

Claude Code will not honor project-scoped ``permissions.allow`` grants from a
repo's committed ``.claude/settings.json`` until the workspace has been
trusted, and (as of Claude Code 2.1.193) it says so on stderr. That is the
right default -- a fresh clone must not be able to grant itself tool
permissions just by committing them -- but CI checks this repo out fresh on
every run, so its workspace is never trusted and the grants this repo ships
are silently dropped.

Running this from the workspace root records the same consent a developer
gives by accepting the trust dialog once:
``projects[<workspace>].hasTrustDialogAccepted: true`` in the global Claude
Code config file (``$CLAUDE_CONFIG_DIR/.claude.json`` when that env var is
set, else ``~/.claude.json``). Existing config contents -- e.g. the onboarding
state ``claude install`` writes -- are preserved.

The workspace key matches what Claude Code derives for these CI checkouts: the
physical path of the enclosing git root, falling back to the working directory
(the docker image copies the source without ``.git``), with Windows
backslashes folded to forward slashes. It is deliberately not a full
reimplementation of Claude Code's derivation: linked ``git worktree``
checkouts, which key on the main repository root, are not handled because no
CI job uses one.
"""

import json
import os
from pathlib import Path


def workspace_key(start: Path) -> str:
    """The ``projects`` key Claude Code uses for this workspace's trust flag."""
    # Physical (symlink-resolved) path: Claude Code derives the key from
    # process.cwd(), which the OS already reports physically, so a lexical
    # path here would fail to match on a symlinked checkout.
    start = start.resolve()
    root = next((p for p in (start, *start.parents) if (p / ".git").exists()), start)
    key = str(root)
    # Claude Code keys Windows paths with forward slashes so that C:\x and C:/x
    # resolve to the same entry. Only fold on Windows: on POSIX a backslash is
    # a legal path character, not a separator, and folding it would map two
    # distinct directories to one key.
    return key.replace("\\", "/") if os.name == "nt" else key


def main() -> None:
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home())
    config_path = config_home / ".claude.json"
    # Claude Code writes this file as UTF-8; the platform locale default is not
    # UTF-8 on Windows. A corrupt/unreadable existing file fails loudly on
    # purpose -- silently replacing it with {} would destroy the user's config.
    config = (
        json.loads(config_path.read_text(encoding="utf-8"))
        if config_path.exists()
        else {}
    )
    key = workspace_key(Path.cwd())
    config.setdefault("projects", {}).setdefault(key, {})["hasTrustDialogAccepted"] = (
        True
    )
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Trusted workspace {key!r} in {config_path}")


if __name__ == "__main__":
    main()
