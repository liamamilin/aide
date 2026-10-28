# Aide native shell (macOS 14+)

SwiftUI/AppKit shell around the headless Python core (`python -m ai_desktop.core`, NDJSON over stdio).

- `AideCore` — `CoreClient` (spawns the core, request/response + events) and run streaming.
- `AideShell` — Quick Panel (⌘⌃L: typed requests with or without a selection, actions, openable results), Workspace window (conversations, streaming Markdown chat, screenshots via ⌘⌃S), Settings, selection reader, global hotkeys.
- `Aide` — menu-bar executable. Packaged: starts the frozen core in `Contents/Resources/aide-core`; from `swift run`: runs the core from this source tree.

```bash
cd macos
swift test            # spawns the real core against a loopback fake Ollama
swift run Aide        # needs `ollama serve`; ⌘⌃L on selected text in any app; menu bar ✦ → 打开 Aide
```

First use asks for **Accessibility** permission (System Settings → Privacy & Security). When running
from a terminal, macOS attributes the permission to that terminal app until Aide ships as a signed `.app`.

Env: `AIDE_REPO` (source tree, defaults to this checkout), `AIDE_PYTHON`, `AIDE_DATA_DIR`,
`AIDE_OLLAMA_URL`. Styling uses system fonts and semantic colors; an optional per-user accent can be set with
`defaults write com.milin.aide accentHex -string RRGGBB` (delete the key to go back to the system accent).

## Build a release app

```bash
./scripts/build_native.sh --smoke     # dist/Aide.app + dist/Aide-<version>-<arch>.dmg (+ .sha256)
```

Freezes the core with PyInstaller (`scripts/aide_core.spec`, **no Qt**), builds the Swift shell in release mode,
assembles `Aide.app`, signs it ad-hoc, and smoke-tests that the core starts, creates its database in
`AIDE_DATA_DIR`, and exits with the app. Needs Xcode 15+ and PyInstaller; builds for the machine's architecture.

### Installing an unsigned build

There is no Developer ID certificate yet, so the app is **not notarized** and Gatekeeper blocks a downloaded copy:

1. Drag `Aide.app` to Applications and open it once (it will be refused).
2. System Settings → Privacy & Security → "Open Anyway" next to Aide. Or, in Terminal:
   `xattr -dr com.apple.quarantine /Applications/Aide.app`
3. Allow **Accessibility** (reads the selected text for ⌘⌃L) and, on first use, **Automation** for Notes and
   Reminders when you ask Aide to use them.

An ad-hoc signature changes with every build, so macOS may ask for these permissions again after an update.
History is shared with the Qt app (`~/Library/Application Support/ai-desktop-assistant/`).
