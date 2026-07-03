#!/bin/bash
# build.sh — 一键构建 AI 桌面助手 .app
# 用法:
#   ./scripts/build.sh           # 清理 → 构建 → 签名
#   ./scripts/build.sh --test    # 构建前运行 ruff + pytest
#   ./scripts/build.sh --smoke   # 构建后冒烟测试
#   ./scripts/build.sh --dmg     # 构建后生成 DMG
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="AI桌面助手"
APP="${ROOT}/dist/${APP_NAME}.app"
SIGN_IDENTITY="AI Desktop Assistant"

RUN_TESTS=false
SMOKE=false
BUILD_DMG=false

for arg in "$@"; do
    case "$arg" in
        --test) RUN_TESTS=true ;;
        --smoke) SMOKE=true ;;
        --dmg) BUILD_DMG=true ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

echo "==> AI 桌面助手 — 构建开始"

# ── 运行测试 ──────────────────────────────────────────
if $RUN_TESTS; then
    echo "==> [1/6] 运行 ruff 检查..."
    cd "$ROOT" && ruff check ai_desktop/ tests/

    echo "==> [2/6] 运行 pytest..."
    cd "$ROOT" && python -m pytest -x -q
else
    echo "==> [1/6] 跳过测试 (使用 --test 启用)"
fi

# ── 清理 ──────────────────────────────────────────────
echo "==> [3/6] 清理旧构建产物..."
rm -rf "${ROOT}/build/aide"
rm -rf "${ROOT}/dist/${APP_NAME}.app"
rm -rf "${ROOT}/dist/${APP_NAME}"

# ── 构建 ──────────────────────────────────────────────
echo "==> [4/6] 运行 PyInstaller..."
cd "$ROOT" && pyinstaller scripts/aide.spec

# ── 签名 ──────────────────────────────────────────────
echo "==> [5/6] 签名 .app..."
if [ -d "$APP" ]; then
    codesign -s "$SIGN_IDENTITY" --force "$APP"
    codesign -dvv "$APP" 2>&1 | grep -E "Authority|Team|flags"
    echo "    签名完成"
else
    echo "    ERROR: ${APP} 不存在，跳过签名" >&2
    exit 1
fi

# ── 冒烟测试 ──────────────────────────────────────────
if $SMOKE; then
    echo "==> [6/6] 冒烟测试..."
    "${ROOT}/scripts/smoke_test.sh"
else
    echo "==> [6/6] 跳过冒烟测试 (使用 --smoke 启用)"
fi

# ── DMG ───────────────────────────────────────────────
if $BUILD_DMG; then
    echo "==> 生成 DMG..."
    "${ROOT}/scripts/build_dmg.sh"
fi

echo "==> ✅ ${APP}"
ls -lh "$APP"
echo "==> 构建完成"
