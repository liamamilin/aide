#!/bin/bash
# build_native.sh — build the native Aide.app (SwiftUI shell + frozen Python core) for a GitHub Release.
#
# Usage:
#   ./scripts/build_native.sh            # build dist/Aide.app (signed) and dist/Aide-<ver>-arm64.dmg
#   ./scripts/build_native.sh --smoke    # also launch the built app in isolation and check the core starts/exits
#
# AIDE_SIGN_IDENTITY overrides the signing identity ("-" forces ad-hoc). Not notarized: there is no Developer ID here, so Gatekeeper blocks a downloaded copy until the person
# allows it (see README). The core is frozen without Qt; the bundle carries no brand assets
# (the default icon is generated; pass AIDE_ICON=... for a local build with your own).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
APP="${ROOT}/dist/Aide.app"
WORK="${ROOT}/build/native"
SMOKE=false
for arg in "$@"; do
    case "$arg" in
        --smoke) SMOKE=true ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

cd "$ROOT"
VERSION="$($PYTHON_BIN -c 'from ai_desktop.version import __version__; print(__version__)')"
ARCH="$(uname -m)"
# macOS ties Accessibility/Automation grants to the app's code identity. An ad-hoc signature changes with every
# build, so each rebuild asks again; a stable certificate keeps the grant. Prefer one already in the keychain.
if [[ -n "${AIDE_SIGN_IDENTITY:-}" ]]; then
    SIGN_IDENTITY="$AIDE_SIGN_IDENTITY"
else
    SIGN_IDENTITY="$({
        security find-identity -v -p codesigning 2>/dev/null || true
    } | awk -F '"' '/Developer ID Application:|Apple Development:|AI Desktop Assistant/ { print $2; exit }')"
    SIGN_IDENTITY="${SIGN_IDENTITY:--}"
fi
echo "==> Aide ${VERSION} (${ARCH}) — native build, signing with: $([[ "$SIGN_IDENTITY" == "-" ]] && echo ad-hoc || echo "$SIGN_IDENTITY")"

echo "==> [1/5] freezing the Python core (no Qt)"
rm -rf "$WORK" && mkdir -p "$WORK"
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm --distpath "$WORK/dist" --workpath "$WORK/work" scripts/aide_core.spec >/dev/null
HELPER="$WORK/dist/aide-core"
test -x "$HELPER/aide-core"

echo "==> [2/5] building the Swift shell (release)"
(cd macos && swift build -c release --arch "$ARCH")
BIN="$(cd macos && swift build -c release --arch "$ARCH" --show-bin-path)/Aide"
test -x "$BIN"

echo "==> [3/5] assembling Aide.app"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Aide"
# A neutral generated icon by default; a local build may use its own artwork: AIDE_ICON=/path/to/icon.icns
if [[ -n "${AIDE_ICON:-}" ]]; then
    cp "$AIDE_ICON" "$APP/Contents/Resources/AppIcon.icns"
else
    "$PYTHON_BIN" scripts/make_native_icon.py "$APP/Contents/Resources/AppIcon.icns"
fi
cp -R "$HELPER" "$APP/Contents/Resources/aide-core"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>Aide</string>
    <key>CFBundleIdentifier</key><string>com.milin.aide</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleName</key><string>Aide</string>
    <key>CFBundleDisplayName</key><string>Aide</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleVersion</key><string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppleEventsUsageDescription</key><string>用于按你的要求操作备忘录、提醒事项、日历和窗口。</string>
    <key>NSRemindersUsageDescription</key><string>用于按你的要求查看、完成或删除提醒事项。</string>
    <key>NSRemindersFullAccessUsageDescription</key><string>用于按你的要求查看、完成或删除提醒事项。</string>
</dict>
</plist>
PLIST

echo "==> [4/5] validating and signing"
"$PYTHON_BIN" scripts/release_check.py --version "$VERSION" --native-bundle "$APP"
# --deep trips over PyInstaller's onedir layout (and Contents/Helpers wants every file signed), so the frozen core
# lives in Resources: sign each Mach-O inside-out, then the bundle itself.
while IFS= read -r -d '' file; do
    if file -b "$file" | grep -q "Mach-O"; then codesign --force --sign "$SIGN_IDENTITY" "$file" >/dev/null 2>&1; fi
done < <(find "$APP/Contents/Resources/aide-core" -type f -print0)
codesign --force --sign "$SIGN_IDENTITY" "$APP/Contents/MacOS/Aide"
codesign --force --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --strict "$APP"

echo "==> [5/5] DMG"
DMG="${ROOT}/dist/Aide-${VERSION}-${ARCH}.dmg"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/aide-dmg.XXXXXX")"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP" "$STAGING/Aide.app"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
hdiutil create -volname "Aide" -srcfolder "$STAGING" -format UDZO "$DMG" >/dev/null
(cd "${ROOT}/dist" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")

if $SMOKE; then
    "${ROOT}/scripts/smoke_native.sh" "$APP"
fi

echo "==> ✅ ${APP}"
ls -lh "$DMG"
cat "${DMG}.sha256"
