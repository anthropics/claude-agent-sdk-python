# Engineering Guidelines

Standards for contributing to `claude-agent-sdk-python`. These complement the
quick-reference workflow in [`CLAUDE.md`](CLAUDE.md) and the release process in
[`RELEASING.md`](RELEASING.md).

## Principles

1. **Understand before changing.** Read the relevant module, its tests, and the
   public type surface in `src/claude_agent_sdk/types.py` before editing.
2. **Smallest correct change.** Prefer a surgical fix over a refactor. This is a
   published SDK — churn ripples out to every downstream consumer.
3. **Public API is a contract.** Anything exported from
   `src/claude_agent_sdk/__init__.py` is depended on by users. Additive changes
   only unless a breaking change is deliberate, documented in `CHANGELOG.md`, and
   reflected in a version bump.

## Supported runtimes

- Python **3.10+** (`requires-python = ">=3.10"` in `pyproject.toml`). Do not use
  syntax or stdlib APIs newer than 3.10 without a guarded fallback.
- Async is built on **anyio** (asyncio + trio). Do not import `asyncio`
  directly for anything anyio already abstracts; tests run under both backends.

## Local workflow

```bash
# Lint + autofix, then format
python -m ruff check src/ tests/ --fix
python -m ruff format src/ tests/

# Typecheck (src/ only, must be clean)
python -m mypy src/

# Tests
python -m pytest tests/
python -m pytest tests/test_client.py   # single file
```

All four must be green before you push. CI runs the same commands.

## Code style

- **Fully typed.** The package ships `Typing :: Typed`; every public function and
  method carries complete annotations. `mypy src/` must pass with no new ignores.
- Keep internal implementation under `src/claude_agent_sdk/_internal/`; do not
  leak internal types into the public surface.
- Match the idioms of the file you are editing (naming, docstring style, error
  types). Raise the SDK's own error types rather than bare exceptions.
- No new runtime dependencies without discussion — the runtime deps in
  `pyproject.toml` are intentionally minimal (`anyio`, `sniffio`, `mcp`,
  `typing_extensions` on <3.11). Test-only deps go in the `dev` extra.

## Testing

- Every fix ships with a regression test; every feature ships with coverage of
  its success and failure paths.
- Tests that need optional backends (`examples` extra: boto3/moto/redis/asyncpg)
  must `importorskip` so default CI stays green without them.
- Prefer testing observable behavior over internal calls. Cover malformed input,
  timeouts, and partial/streamed responses — the transport and message parser are
  the sharp edges.

## Security

- Never log or embed API keys, tokens, or session credentials — not in
  exceptions, not in debug output. Redact before surfacing.
- Treat CLI subprocess arguments and environment as trust boundaries; validate
  and quote anything derived from caller input in
  `_internal/transport/subprocess_cli.py`.
- Fail closed on malformed transport data rather than guessing.

## Commits & pull requests

- Conventional-commit prefixes, matching existing history: `fix:`, `feat:`,
  `docs:`, `chore:`, `refactor:`, `test:`.
- One logical change per PR. Update `CHANGELOG.md` for any user-visible change.
- PRs must pass ruff, mypy, and pytest. Open as a draft until CI is green.
