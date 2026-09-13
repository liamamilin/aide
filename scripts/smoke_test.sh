#!/bin/bash
# smoke_test.sh — launch only the packaged executable and wait for its self-test exit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${ROOT}/dist/AI桌面助手.app"
EXECUTABLE="${APP_PATH}/Contents/MacOS/AI桌面助手"
PYTHON_BIN="${PYTHON:-python3}"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aide-smoke.XXXXXX")"
OUTPUT="${TEMP_ROOT}/process.log"
PID=""

cleanup() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
    rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT INT TERM

if [ ! -x "$EXECUTABLE" ]; then
    echo "ERROR: packaged executable not found: ${EXECUTABLE}" >&2
    exit 1
fi

VERSION="$($PYTHON_BIN -c 'from ai_desktop.version import __version__; print(__version__)')"
BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${APP_PATH}/Contents/Info.plist")"

echo "==> Smoke environment"
echo "    macOS: $(sw_vers -productVersion) ($(uname -m))"
echo "    Source/bundle version: ${VERSION}/${BUNDLE_VERSION}"
echo "==> Launching packaged executable with isolated data and logs"

AIDE_SMOKE_TEST=1 \
AIDE_DATA_DIR="${TEMP_ROOT}/data" \
AIDE_LOG_DIR="${TEMP_ROOT}/logs" \
"$EXECUTABLE" >"$OUTPUT" 2>&1 &
PID=$!

for _ in {1..40}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        break
    fi
    sleep 0.25
done

if kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: packaged app did not complete smoke mode within 10 seconds" >&2
    cat "$OUTPUT" >&2
    exit 1
fi

set +e
wait "$PID"
STATUS=$?
set -e
PID=""

if [ "$STATUS" -ne 0 ]; then
    echo "ERROR: packaged app exited with status ${STATUS}" >&2
    cat "$OUTPUT" >&2
    exit "$STATUS"
fi
if [ ! -f "${TEMP_ROOT}/data/chat_history.db" ]; then
    echo "ERROR: isolated chat database was not initialized" >&2
    cat "$OUTPUT" >&2
    exit 1
fi
if [ ! -f "${TEMP_ROOT}/logs/app.log" ]; then
    echo "ERROR: isolated application log was not created" >&2
    cat "$OUTPUT" >&2
    exit 1
fi

echo "==> Packaged app initialized and exited cleanly — SMOKE TEST PASSED"
