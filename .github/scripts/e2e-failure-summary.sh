#!/usr/bin/env bash
# Prints the failures from a pytest log as one annotation-safe line: each
# FAILED/ERROR line, the assertion lines (`E   ...`) that explain them, and
# pytest's closing summary.
#
# Usage: e2e-failure-summary.sh <pytest log>
set -euo pipefail

log=$1
{
  grep -E '^(FAILED|ERROR) ' "$log" || true
  grep -E '^E ' "$log" | head -n 40 || true
  tail -n 5 "$log"
} | sed ':a;N;$!ba;s/%/%25/g;s/\r/%0D/g;s/\n/%0A/g'
