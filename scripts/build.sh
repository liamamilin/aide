#!/bin/bash
# build.sh — build and validate the macOS application bundle.
#
# Usage:
#   ./scripts/build.sh                    # build + ad-hoc sign + validate
#   ./scripts/build.sh --test             # run ruff/pytest before building
#   ./scripts/build.sh --smoke            # launch the packaged executable in isolation
#   ./scripts/build.sh --dmg              # also create a versioned DMG
#   AIDE_SIGN_IDENTITY="Developer ID Application: ..." ./scripts/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="AI桌面助手"
APP="${ROOT}/dist/${APP_NAME}.app"
PYTHON_BIN="${PYTHON:-python3}"
SIGN_IDENTITY="${AIDE_SIGN_IDENTITY:--}"

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

cd "$ROOT"
VERSION="$($PYTHON_BIN -c 'from ai_desktop.version import __version__; print(__version__)')"

echo "==> AI 桌面助手 ${VERSION} — 构建开始"
echo "    macOS: $(sw_vers -productVersion) ($(uname -m))"
echo "    Python: $($PYTHON_BIN --version 2>&1)"
echo "    PyInstaller: $($PYTHON_BIN -m PyInstaller --version)"

"$PYTHON_BIN" scripts/release_check.py --version "$VERSION"

if $RUN_TESTS; then
    echo "==> [1/6] 运行 ruff 检查..."
    "$PYTHON_BIN" -m ruff check ai_desktop/ tests/ scripts/release_check.py scripts/restore_database.py scripts/benchmark_m2.py scripts/benchmark_ocr.py

    echo "==> [2/6] 运行 pytest..."
    QT_QPA_PLATFORM=offscreen "$PYTHON_BIN" -m pytest tests/ -q
else
    echo "==> [1/6] 跳过测试（使用 --test 启用）"
fi

echo "==> [3/6] 清理当前应用构建目录..."
rm -rf "${ROOT}/build/aide" "$APP" "${ROOT}/dist/${APP_NAME}"

echo "==> [4/6] 使用 scripts/aide.spec 构建..."
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm scripts/aide.spec

if [ ! -d "$APP" ]; then
    echo "ERROR: ${APP} 不存在" >&2
    exit 1
fi

echo "==> [5/6] 签名并验证 .app..."
if [ "$SIGN_IDENTITY" = "-" ]; then
    echo "    使用 ad-hoc 签名；该候选包未经过 Developer ID 签名或公证"
fi
codesign --force --deep --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
"$PYTHON_BIN" scripts/release_check.py --bundle "$APP" --version "$VERSION"

if $SMOKE; then
    echo "==> [6/6] 运行隔离冒烟测试..."
    "${ROOT}/scripts/smoke_test.sh"
    "${ROOT}/scripts/upgrade_smoke_test.sh"
else
    echo "==> [6/6] 跳过冒烟测试（使用 --smoke 启用）"
fi

if $BUILD_DMG; then
    echo "==> 生成 DMG..."
    "${ROOT}/scripts/build_dmg.sh" "$VERSION"
fi

echo "==> ✅ ${APP}"
du -sh "$APP"
echo "==> 构建完成"
