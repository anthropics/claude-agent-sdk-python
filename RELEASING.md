# Releasing

There are two ways to release the SDK: **automatic** (triggered by a CLI version bump) and **manual** (triggered via GitHub Actions UI).

Both flows call the same reusable `build-and-publish.yml` workflow, which builds platform-specific wheels on 4 OS targets (Ubuntu x86, Ubuntu ARM, macOS, Windows), publishes to PyPI, updates version files, generates a changelog entry using Claude, pushes to `main`, and creates a git tag + GitHub Release.

## Versioning

The project tracks two separate version numbers:

- **SDK version** — in `pyproject.toml` and `src/claude_agent_sdk/_version.py`
- **Bundled CLI version** — in `src/claude_agent_sdk/_cli_version.py`

Both follow semver (`MAJOR.MINOR.PATCH`). Git tags use the format `vX.Y.Z`.

## Automatic Release (CLI Version Bump)

This is the most common release path. Every CLI version bump automatically produces a new SDK patch release.

**Flow:**

1. A commit with message `chore: bump bundled CLI version to X.Y.Z` is pushed to `main`, updating `_cli_version.py`.
2. The `Test` workflow runs on that push.
3. On successful completion, `auto-release.yml` fires via `workflow_run`.
4. It verifies the trigger commit message and that `_cli_version.py` changed.
5. It reads the current SDK version from `_version.py` and increments the patch number (e.g., `0.1.24` → `0.1.25`).
6. It calls `build-and-publish.yml`, which builds, publishes, pushes, tags, and creates a GitHub Release.

**Typical commit log after an auto-release:**
```
ccdf20a chore: bump bundled CLI version to 2.1.25
baf9bc3 chore: release v0.1.25
```

## Manual Release

Use this when you need to release with a specific version number (e.g., for minor/major bumps or non-CLI-bump changes).

**Flow:**

1. Go to [**Actions → Publish to PyPI**](https://github.com/anthropics/claude-agent-sdk-python/actions/workflows/publish.yml) and click **Run workflow**.
2. Enter the desired version (e.g., `0.2.0`).
3. The workflow runs the full test suite (Python 3.10–3.13) and lint checks.
4. On success, it calls `build-and-publish.yml`, which builds, publishes, pushes, tags, and creates a GitHub Release.

## Scripts

All release-related scripts live in `scripts/`:

| Script | Purpose |
|---|---|
| `update_version.py` | Updates SDK version in `pyproject.toml` and `_version.py` |
| `update_cli_version.py` | Updates CLI version in `_cli_version.py` |
| `build_wheel.py` | Downloads the CLI binary, builds the wheel, retags with platform-specific tags |
| `download_cli.py` | Downloads the Claude Code CLI binary for the current platform |

## Required Secrets

| Secret | Used For |
|---|---|
| `PYPI_API_TOKEN` | Publishing to PyPI |
| `ANTHROPIC_API_KEY` | Changelog generation and e2e tests |
| `DEPLOY_KEY` | SSH key for direct pushes to `main` |
