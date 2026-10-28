#!/bin/bash
# smoke_native.sh <Aide.app> — launch the built app against a throwaway data dir and check the core lifecycle.
# Only this run's own child process is inspected, so another Aide already running does not confuse it.
set -euo pipefail
APP="${1:?usage: smoke_native.sh path/to/Aide.app}"
DATA="$(mktemp -d "${TMPDIR:-/tmp}/aide-smoke.XXXXXX")"
PID=""
trap 'rm -rf "$DATA"; [[ -n "$PID" ]] && kill "$PID" 2>/dev/null || true' EXIT

AIDE_DATA_DIR="$DATA" "$APP/Contents/MacOS/Aide" >/dev/null 2>"$DATA/stderr.log" &
PID=$!
CORE=""
for _ in $(seq 1 50); do
    CORE="$(pgrep -P "$PID" -f "aide-core/aide-core" | head -1 || true)"
    [[ -n "$CORE" ]] && break
    sleep 0.2
done
[[ -n "$CORE" ]] || { echo "FAIL: the app did not start its core"; cat "$DATA/stderr.log"; exit 1; }
# The process appears before it has opened its database; give it a few seconds.
for _ in $(seq 1 50); do test -f "$DATA/chat_history.db" && break; sleep 0.2; done
test -f "$DATA/chat_history.db" || { echo "FAIL: core did not create its database in AIDE_DATA_DIR"; cat "$DATA/stderr.log"; exit 1; }
kill "$PID"; wait "$PID" 2>/dev/null || true; PID=""
for _ in $(seq 1 50); do
    kill -0 "$CORE" 2>/dev/null || { echo "smoke: core started, wrote its database, and exited with the app"; exit 0; }
    sleep 0.2
done
echo "FAIL: core still running after the app exited"; kill "$CORE" 2>/dev/null || true; exit 1
