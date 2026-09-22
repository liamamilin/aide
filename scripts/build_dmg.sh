#!/bin/bash
# build_dmg.sh — create a versioned DMG from the validated PyInstaller bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="AI桌面助手"
APP="${ROOT}/dist/${APP_NAME}.app"
PYTHON_BIN="${PYTHON:-python3}"
SOURCE_VERSION="$($PYTHON_BIN -c 'from ai_desktop.version import __version__; print(__version__)')"
VERSION="${1:-$SOURCE_VERSION}"
VERSION="${VERSION#v}"
DMG_PATH="${ROOT}/dist/${APP_NAME}-${VERSION}.dmg"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/${APP_NAME}-dmg.XXXXXX")"

cleanup() {
    rm -rf "$STAGING"
}
trap cleanup EXIT INT TERM

cd "$ROOT"
"$PYTHON_BIN" scripts/release_check.py --bundle "$APP" --version "$VERSION"

echo "==> Building DMG: $(basename "$DMG_PATH")"
cp -R "$APP" "$STAGING/${APP_NAME}.app"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG_PATH"

if command -v create-dmg >/dev/null 2>&1; then
    echo "==> Using create-dmg"
    create-dmg \
        --volname "$APP_NAME" \
        --volicon "${ROOT}/ai_desktop/图标-v2.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 150 190 \
        --hide-extension "${APP_NAME}.app" \
        --app-drop-link 450 190 \
        "$DMG_PATH" \
        "$STAGING"
else
    echo "==> create-dmg not found, using hdiutil"
    hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" \
        -format UDZO "$DMG_PATH"
fi

echo "==> DMG ready: ${DMG_PATH}"
ls -lh "$DMG_PATH"
shasum -a 256 "$DMG_PATH"
