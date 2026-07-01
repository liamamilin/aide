#!/bin/bash
# smoke_test.sh — 验证打包后的 .app 基础功能
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${ROOT}/dist/AI桌面助手.app"

if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: ${APP_PATH} not found"
    exit 1
fi

echo "==> Launching ${APP_PATH} ..."
open "$APP_PATH"
PID=$(pgrep -f "AI桌面助手" || true)
if [ -z "$PID" ]; then
    echo "ERROR: App did not start"
    exit 1
fi
echo "==> App started (PID: $PID)"

echo "==> Waiting 5 seconds ..."
sleep 5

if kill -0 "$PID" 2>/dev/null; then
    echo "==> App still running after 5s — SMOKE TEST PASSED"
    echo "==> Cleaning up ..."
    kill "$PID" 2>/dev/null || true
    sleep 1
else
    echo "ERROR: App crashed within 5 seconds"
    exit 1
fi
echo "==> Done"
