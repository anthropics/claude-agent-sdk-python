---
name: verify
description: Drive this repo's build/release scripts end-to-end without touching the network. Use when verifying changes to scripts/download_cli.py, scripts/build_wheel.py, scripts/update_cli_version.py, or scripts/_cli_version_validation.py.
---

# Verifying the build scripts

The SDK itself is a library (drive it through `import claude_agent_sdk`), but
the `scripts/` directory is a set of **CLIs** run by the release workflows.
Verify them by running them, not by importing them.

## NEVER run the real installer

`scripts/download_cli.py` shells out to `curl https://claude.ai/install.sh | bash`
(and the PowerShell equivalent). Running it for real **overwrites the `claude`
binary on the developer's machine**. Do not run `bash install.sh`, and do not
invoke `download_cli.py` with `latest`/`stable` (or any case variant) against
the real network.

Instead, put stub `curl` / `bash` / `powershell` on a temp `PATH` and drive the
script under `env -i` with an isolated `HOME` (so `find_installed_cli()` cannot
discover the real binary and copy it into `src/claude_agent_sdk/_bundled/`).

## The harness

```bash
SB=$(mktemp -d); mkdir -p $SB/stub $SB/home $SB/repo/src/claude_agent_sdk
cp scripts/{download_cli.py,build_wheel.py,update_cli_version.py,_cli_version_validation.py} $SB/repo/scripts/
printf '__cli_version__ = "2.1.208"\n' > $SB/repo/src/claude_agent_sdk/_cli_version.py

cat > $SB/stub/curl <<'EOF'
#!/bin/bash
out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
echo "[stub curl] argv: $*" >> "$MARKER"
[ -n "$out" ] && printf '%b' "${CURL_BODY:-#!/bin/bash\necho hi\n}" > "$out"
exit ${CURL_EXIT:-0}
EOF
cat > $SB/stub/bash <<'EOF'
#!/bin/bash
{ echo "[stub bash] argc=$#"; for a in "$@"; do echo "    <$a>"; done; } >> "$MARKER"
EOF
chmod +x $SB/stub/*

cd $SB/repo
env -i HOME=$SB/home PATH=$SB/stub:/usr/bin:/bin MARKER=$SB/m \
    CLAUDE_CLI_VERSION=2.1.208 .venv/bin/python scripts/download_cli.py
```

`run_command()` captures the child's output, so the stubs must log to a
`$MARKER` file — printing to stderr is swallowed.

## Flows worth driving

- **`update_cli_version.py <version>`** — the whole validator surface is
  reachable here: a concrete version writes the file; `latest`/`stable`,
  `v2.1.207`, `next`, `2.1`, and a quote-breakout string each exit 1 with a
  distinct message and leave the file untouched.
- **`build_wheel.py --skip-sdist`** — reads the pin, then chains into
  `download_cli.py`. Rewrite `_cli_version.py` in the sandbox to a moving tag /
  single quotes / garbage / delete it: each must fail *before* any subprocess
  is spawned. `--cli-version <v>` bypasses the pin (intentional escape hatch;
  the release workflow does not use it).
- **`download_cli.py`** — set `CURL_BODY` to an HTML error page or an empty
  string to prove the body check refuses it with no retry; empty the `PATH` to
  prove a missing `curl` fails fast in one attempt; `CURL_EXIT=22` to prove a
  genuine transient failure still retries 3×.
- **The Windows path is unreachable on Linux.** It is behind
  `platform.system() == "Windows"`. Drive it with a small runner that
  `patch.object(m.platform, "system", return_value="Windows")` and a stub
  `powershell` that reads the `-Command` text and honours
  `$env:CLAUDE_CLI_INSTALL_SCRIPT`. This is the one place where forcing the
  platform is legitimate.

## Gotchas

- `${CURL_BODY:-default}` treats an *empty* body as unset. Use a separate stub
  that does `: > "$out"` to test the empty-body branch.
- The retry path really sleeps (jitter up to 5s, then 2s + 4s). A full
  three-attempt run takes ~10s; budget for it rather than assuming a hang.
