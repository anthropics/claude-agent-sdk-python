#!/usr/bin/env bash
# Runs the e2e tests on a macOS runner as a separate user whose outbound network
# traffic is limited to the Claude API by the OS packet filter (pf). GitHub's
# egress-firewall runner is Linux only, so this stands in for it on macOS.
#
# The runner user has passwordless sudo, so the tests (and anything Claude runs
# during them) must not run as that user: they could turn the filter off. The
# e2e user has no admin rights and no sudo.
#
# Limits: DNS lookups go through mDNSResponder, not the e2e user's own process,
# so they are not filtered. Only TCP and UDP are filtered.
#
# Usage: run-e2e-firewalled-macos.sh <pytest args...>
# Needs ANTHROPIC_IDENTITY_TOKEN_FILE and the ANTHROPIC_* federation variables,
# `python` from actions/setup-python and `claude` on PATH.
set -euo pipefail

E2E_USER=claude-e2e
E2E_UID=7001
E2E_HOME=/Users/$E2E_USER
BIN_DIR=/opt/claude-e2e/bin
# Published address ranges of api.anthropic.com
API_V4=160.79.104.0/23
API_V6=2607:6bc0::/48

python_bin=$(python -c 'import sys; print(sys.executable)')
claude_bin=$(python -c 'import os, shutil; print(os.path.realpath(shutil.which("claude")))')

echo "::group::Create the e2e user"
# A group of its own, so it gets no write access that the runner's groups have.
sudo dscl . -create "/Groups/$E2E_USER"
sudo dscl . -create "/Groups/$E2E_USER" PrimaryGroupID "$E2E_UID"
sudo dscl . -create "/Users/$E2E_USER"
sudo dscl . -create "/Users/$E2E_USER" UniqueID "$E2E_UID"
sudo dscl . -create "/Users/$E2E_USER" PrimaryGroupID "$E2E_UID"
sudo dscl . -create "/Users/$E2E_USER" UserShell /bin/bash
sudo dscl . -create "/Users/$E2E_USER" NFSHomeDirectory "$E2E_HOME"
sudo mkdir -p "$E2E_HOME/tmp"
sudo chown -R "$E2E_USER:$E2E_USER" "$E2E_HOME"
# The user needs to read the checkout, the toolcache and the token file, and
# must not be able to write anything the runner uses after this step (the
# checkout's .git above all), so it gets search access and nothing more. The
# token refresher truncates the file in place, so this mode survives refreshes.
sudo chmod o+x "$HOME" "$HOME/work" "$RUNNER_TEMP"
sudo chgrp "$E2E_USER" "$ANTHROPIC_IDENTITY_TOKEN_FILE"
sudo chmod 640 "$ANTHROPIC_IDENTITY_TOKEN_FILE"
sudo mkdir -p "$BIN_DIR"
sudo cp "$claude_bin" "$BIN_DIR/claude"
sudo chmod 755 "$BIN_DIR/claude"
echo "::endgroup::"

echo "::group::Limit the e2e user's outbound traffic to the Claude API"
rules=$(mktemp)
cat > "$rules" <<EOF
pass out quick on lo0 all
pass out quick inet proto tcp to $API_V4 port 443 user $E2E_USER
pass out quick inet6 proto tcp to $API_V6 port 443 user $E2E_USER
block return out quick proto { tcp udp } all user $E2E_USER
EOF
cat "$rules"
# Load the default ruleset first: it references the com.apple/* anchors.
sudo pfctl -q -f /etc/pf.conf
sudo pfctl -q -a "com.apple/claude-e2e" -f "$rules"
sudo pfctl -E 2>&1 | grep -v -i "^no ALTQ" || true
sudo pfctl -a "com.apple/claude-e2e" -s rules
echo "::endgroup::"

as_e2e() {
  sudo -u "$E2E_USER" -H env \
    PATH="$BIN_DIR:$(dirname "$python_bin"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    TMPDIR="$E2E_HOME/tmp" \
    ANTHROPIC_FEDERATION_RULE_ID="${ANTHROPIC_FEDERATION_RULE_ID:-}" \
    ANTHROPIC_ORGANIZATION_ID="${ANTHROPIC_ORGANIZATION_ID:-}" \
    ANTHROPIC_SERVICE_ACCOUNT_ID="${ANTHROPIC_SERVICE_ACCOUNT_ID:-}" \
    ANTHROPIC_WORKSPACE_ID="${ANTHROPIC_WORKSPACE_ID:-}" \
    ANTHROPIC_IDENTITY_TOKEN_FILE="$ANTHROPIC_IDENTITY_TOKEN_FILE" \
    "$@"
}

echo "::group::Check the filter before running the tests"
rc=0
blocked=$(as_e2e curl -sS -m 10 -o /dev/null https://example.com 2>&1) || rc=$?
# 7: could not connect, 28: timed out. Anything else is not the firewall's doing.
if [ "$rc" -ne 7 ] && [ "$rc" -ne 28 ]; then
  echo "::error::https://example.com was not refused by the OS firewall (curl exit $rc): $blocked"
  exit 1
fi
reached=$(as_e2e curl -sS -m 15 -o /dev/null -w '%{http_code}' https://api.anthropic.com/ 2>&1 || true)
code=${reached: -3}
if ! [[ "$code" =~ ^[1-5][0-9][0-9]$ ]]; then
  echo "::error::The e2e user could not reach https://api.anthropic.com: $reached"
  exit 1
fi
echo "Blocked https://example.com; reached https://api.anthropic.com (HTTP $code)."
echo "::endgroup::"

# Nothing the e2e user started may outlive this step.
trap 'sudo pkill -KILL -u "$E2E_USER" || true' EXIT

cd "$GITHUB_WORKSPACE"
as_e2e "$python_bin" scripts/trust_workspace.py
log=$(mktemp)
if ! as_e2e "$python_bin" -m pytest -p no:cacheprovider "$@" 2>&1 | tee "$log"; then
  # Repeat the failures and pytest's summary as an annotation, where they show
  # without opening the log.
  echo "::error title=e2e tests failed::$("$GITHUB_WORKSPACE/.github/scripts/e2e-failure-summary.sh" "$log")"
  exit 1
fi
