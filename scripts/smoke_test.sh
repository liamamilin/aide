#!/bin/bash
# smoke_test.sh — launch only the packaged executable and wait for its self-test exit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${ROOT}/dist/AI桌面助手.app"
EXECUTABLE="${APP_PATH}/Contents/MacOS/AI桌面助手"
PYTHON_BIN="${PYTHON:-python3}"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aide-smoke.XXXXXX")"
OUTPUT="${TEMP_ROOT}/process.log"
OCR_OUTPUT="${TEMP_ROOT}/ocr-runtime.json"
SPEECH_OUTPUT="${TEMP_ROOT}/speech-runtime.json"
SEARCH_OUTPUT="${TEMP_ROOT}/search-runtime.json"
EXECUTION_OUTPUT="${TEMP_ROOT}/execution-runtime.json"
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
echo "==> Checking packaged Apple Vision OCR runtime"
"$EXECUTABLE" --ocr-runtime >"$OCR_OUTPUT" 2>&1
"$PYTHON_BIN" -c 'import json, sys; value = json.load(open(sys.argv[1])); assert value["engine"] == "apple-vision"; assert value["languages"]' "$OCR_OUTPUT"
echo "==> Checking packaged English speech runtime"
"$EXECUTABLE" --speech-runtime >"$SPEECH_OUTPUT" 2>&1
"$PYTHON_BIN" -c 'import json, sys; value = json.load(open(sys.argv[1])); assert value == {"engine": "kokoro", "language": "en-us", "status": "ready"}' "$SPEECH_OUTPUT"
echo "==> Checking packaged search Keychain bridge (no credential access)"
"$EXECUTABLE" --search-runtime >"$SEARCH_OUTPUT" 2>&1
"$PYTHON_BIN" -c 'import json, sys; value = json.load(open(sys.argv[1])); assert value == {"keychain_available": True, "providers": ["parallel", "exa"]}' "$SEARCH_OUTPUT"
echo "==> Checking packaged Bash runtime with a fixed temporary read-only fixture"
"$EXECUTABLE" --execution-runtime >"$EXECUTION_OUTPUT" 2>&1
"$PYTHON_BIN" -c 'import json, sys; value = json.load(open(sys.argv[1])); assert value == {"engine": "bash", "status": "ready", "automatic_mode": "fixed_argv"}' "$EXECUTION_OUTPUT"
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
EXPECTED_SCHEMA="$($PYTHON_BIN -c 'from ai_desktop.utils.storage import SCHEMA_VERSION; print(SCHEMA_VERSION)')"
ACTUAL_SCHEMA="$($PYTHON_BIN -c 'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); print(db.execute("PRAGMA user_version").fetchone()[0]); db.close()' "${TEMP_ROOT}/data/chat_history.db")"
if [ "$ACTUAL_SCHEMA" != "$EXPECTED_SCHEMA" ]; then
    echo "ERROR: isolated database schema ${ACTUAL_SCHEMA}, expected ${EXPECTED_SCHEMA}" >&2
    cat "$OUTPUT" >&2
    exit 1
fi
if [ ! -f "${TEMP_ROOT}/logs/app.log" ]; then
    echo "ERROR: isolated application log was not created" >&2
    cat "$OUTPUT" >&2
    exit 1
fi
"$PYTHON_BIN" -c 'from pathlib import Path; import sys; text = Path(sys.argv[1]).read_text(); assert "Fluent command card initialized and invalidated without execution" in text' "${TEMP_ROOT}/logs/app.log"
echo "==> Packaged Fluent command card confirmation/output lifecycle passed"
"$PYTHON_BIN" -c 'from pathlib import Path; import sys; text = Path(sys.argv[1]).read_text(); assert "Fluent search card and verified citations initialized without network" in text' "${TEMP_ROOT}/logs/app.log"
echo "==> Packaged Fluent search card and verified citations passed without network"
"$PYTHON_BIN" -c 'from pathlib import Path; import sys; text = Path(sys.argv[1]).read_text(); assert "Audit schema retention and read-only history verified without tools" in text' "${TEMP_ROOT}/logs/app.log"
echo "==> Packaged audit schema, retention and read-only history passed without tools"
"$PYTHON_BIN" -c 'from pathlib import Path; import sys; text = Path(sys.argv[1]).read_text(); assert "Explicit task authorization and model-step cards verified without execution" in text' "${TEMP_ROOT}/logs/app.log"
echo "==> Packaged explicit task authorization and model-step cards passed without execution"

echo "==> Packaged app initialized schema ${ACTUAL_SCHEMA} and exited cleanly — SMOKE TEST PASSED"
