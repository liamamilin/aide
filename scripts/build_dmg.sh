#!/bin/bash
# build_dmg.sh — 从 PyInstaller 产物生成 DMG 安装包
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="AI桌面助手"
APP="${ROOT}/dist/${APP_NAME}.app"
VERSION="${1:-$(cd "$ROOT" && git describe --tags --dirty=-dirty 2>/dev/null || echo "1.0.0")}"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
DMG_PATH="${ROOT}/dist/${DMG_NAME}"
STAGING="/tmp/${APP_NAME}-dmg-staging"

echo "==> Building DMG: ${DMG_NAME}"

if [ ! -d "$APP" ]; then
    echo "ERROR: ${APP} not found — run pyinstaller first"
    exit 1
fi

# 清理上次 staging
rm -rf "$STAGING"
mkdir -p "$STAGING"

# 复制 .app
cp -R "$APP" "$STAGING/${APP_NAME}.app"

# 创建 Applications 快捷方式
ln -s /Applications "$STAGING/Applications"

if command -v create-dmg &>/dev/null; then
    echo "==> Using create-dmg"
    create-dmg \
        --volname "${APP_NAME}" \
        --volicon "${ROOT}/ai_desktop/图标.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 150 190 \
        --hide-extension "${APP_NAME}.app" \
        --app-drop-link 450 190 \
        "${DMG_PATH}" \
        "$STAGING" 2>&1
else
    echo "==> create-dmg not found, using hdiutil"
    rm -f "${DMG_PATH}"
    hdiutil create -volname "${APP_NAME}" -srcfolder "$STAGING" \
        -ov -format UDZO "${DMG_PATH}" 2>&1
fi

echo "==> DMG ready: ${DMG_PATH}"
ls -lh "$DMG_PATH"

# 清理
rm -rf "$STAGING"
