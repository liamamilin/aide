#!/bin/bash
# smoke_native.sh <Aide.app> — launch the built app against a throwaway data dir and check the core lifecycle.
set -euo pipefail
APP="${1:?usage: smoke_native.sh path/to/Aide.app}"
DATA="$(mktemp -d "${TMPDIR:-/tmp}/aide-smoke.XXXXXX")"
trap 'rm -rf "$DATA"; [[ -n "${PID:-}" ]] && kill "$PID" 2>/dev/null || true' EXIT

AIDE_DATA_DIR="$DATA" "$APP/Contents/MacOS/Aide" >/dev/null 2>"$DATA/stderr.log" &
PID=$!
for _ in $(seq 1 50); do
    pgrep -f "$APP/Contents/Resources/aide-core/aide-core" >/dev/null && break
    sleep 0.2
done
pgrep -f "$APP/Contents/Resources/aide-core/aide-core" >/dev/null || { echo "FAIL: core did not start"; cat "$DATA/stderr.log"; exit 1; }
test -f "$DATA/chat_history.db" || { echo "FAIL: core did not create its database in AIDE_DATA_DIR"; exit 1; }
kill "$PID"; wait "$PID" 2>/dev/null || true; PID=""
for _ in $(seq 1 50); do
    pgrep -f "$APP/Contents/Resources/aide-core/aide-core" >/dev/null || { echo "smoke: core started, wrote its database, and exited with the app"; exit 0; }
    sleep 0.2
done
echo "FAIL: core still running after the app exited"; exit 1
