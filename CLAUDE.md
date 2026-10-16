# Workflow

```bash
# Lint and style
# Check for issues and fix automatically
python -m ruff check src/ tests/ scripts/ --fix
python -m ruff format src/ tests/ scripts/

# Typecheck
python -m mypy src/ scripts/

# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_client.py
```

# Codebase Structure

- `src/claude_agent_sdk/` - Main package
  - `client.py` - ClaudeSDKClient for interactive sessions
  - `query.py` - One-shot query function
  - `types.py` - Type definitions
  - `_internal/` - Internal implementation details
    - `transport/subprocess_cli.py` - CLI subprocess management
    - `message_parser.py` - Message parsing logic

# Security hardening for GitHub Actions

Workflow jobs in this repository that call Claude run with three protections.
Keep them when you add or edit a workflow.

1. **Egress-firewall runner.** The job has `runs-on: ubuntu-24.04-firewall`,
   a GitHub-hosted runner that filters the job's outbound network traffic. Do
   not move a job that calls Claude to another runner.
2. **Network allow list.** `.github/egress-firewall.yaml` lists the hosts
   those jobs may reach, besides any that GitHub's firewall allows by default.
   Keep `mode: enforce`, which is what makes the firewall block the rest. Follow
   that file's header when you add a host.
3. **Auto permission mode.** Every step that runs the Claude Code action
   (`uses: anthropics/claude-code-action`, or a local action with a
   `claude_args` input) passes `--permission-mode auto` in `claude_args`. A tool
   call that needs permission and that the allowed tools do not cover then runs
   only if Claude Code's safety review passes it. Allow only the tools the job
   needs, and keep any `--disallowedTools` list a step has. Use `claude-opus-4-6`
   or a newer model: on an older one Claude Code falls back to its default
   permission mode.

`claude.yml` answers `@claude` mentions. The Claude Code action sets
`--permission-mode acceptEdits` for those, and the `--permission-mode auto` in
the workflow's `claude_args`, which comes after it, replaces it.

`build-and-publish.yml`'s `publish` and `release` jobs hold the publishing
credentials (the PyPI token and the deploy key) and do not call Claude. Claude
writes the changelog in `generate-changelog.yml`, a job with none of those
credentials that runs after the upload to PyPI, and the `release` job takes only
`CHANGELOG.md` from it. Both jobs refuse that file unless it is a plain text file
with no credential-like strings in it. Keep it that way.

Exception (listed with its reason in an exemption table in
`.github/scripts/check_workflow_hardening.py`): `test.yml`'s `test-e2e` job runs
on Linux, macOS and Windows. Its matrix uses `ubuntu-24.04-firewall` for Linux.
GitHub offers no such runner for macOS or Windows, so there the tests run
through `.github/scripts/run-e2e-firewalled-macos.sh` and
`.github/scripts/run-e2e-firewalled-windows.ps1`: as a separate
non-administrator user whose outbound traffic the OS firewall limits to the
Claude API. Keep running them that way. The check makes sure the matrix's only
Linux entry is `ubuntu-24.04-firewall`, but it does not look at those steps:
keep the two scripts by hand.

`.github/workflows/workflow-hardening.yml` fails when a job that runs the Claude
Code action or mentions `ANTHROPIC_FEDERATION_RULE_ID` breaks protection 1 or 3,
or when the allow list is missing, empty, not `mode: enforce`, or names a host
with `*`. It cannot see a job that calls Claude another way, so check new
workflows by hand too. If a job cannot meet protection 1 or 3, add it with the
reason to the matching exemption table in
`.github/scripts/check_workflow_hardening.py`. A job in `EXEMPT_FROM_AUTO_MODE`
must set no permission mode at all. Do not skip or weaken the check.

Keep each workflow's `permissions:` block minimal, and never print tokens or
environment variables in workflow logs.
