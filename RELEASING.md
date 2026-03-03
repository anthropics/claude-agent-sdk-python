# Releasing

Releases are triggered manually via GitHub Actions UI using the `build-and-publish.yml` workflow, which builds the package, publishes to PyPI, updates version files, generates a changelog entry using Claude, pushes to `main`, and creates a git tag + GitHub Release.

## Versioning

The project tracks a single version number:

- **SDK version** — in `pyproject.toml` and `src/claude_agent_sdk/_version.py`

Versions follow semver (`MAJOR.MINOR.PATCH`). Git tags use the format `vX.Y.Z`.

## Manual Release

1. Go to [**Actions → Publish to PyPI**](https://github.com/anthropics/claude-agent-sdk-python/actions/workflows/publish.yml) and click **Run workflow**.
2. Enter the desired version (e.g., `0.2.0`).
3. The workflow runs the full test suite (Python 3.10–3.13) and lint checks.
4. On success, it calls `build-and-publish.yml`, which builds, publishes, pushes, tags, and creates a GitHub Release.

## Scripts

All release-related scripts live in `scripts/`:

| Script | Purpose |
|---|---|
| `update_version.py` | Updates SDK version in `pyproject.toml` and `_version.py` |

## Required Secrets

| Secret | Used For |
|---|---|
| `PYPI_API_TOKEN` | Publishing to PyPI |
| `ANTHROPIC_API_KEY` | Changelog generation and e2e tests |
| `DEPLOY_KEY` | SSH key for direct pushes to `main` |
