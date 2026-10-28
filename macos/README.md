# Aide native shell (macOS 14+)

SwiftUI/AppKit shell around the headless Python core (`python -m ai_desktop.core`, NDJSON over stdio).

- `AideCore` — `CoreClient` (spawns the core, request/response + events) and run streaming.
- `AideShell` — Quick Panel (⌘⌃L) and Workspace window (conversations, streaming chat), selection reader, global hotkey.
- `Aide` — menu-bar executable (dev launcher: runs the core from this source tree).

```bash
cd macos
swift test            # spawns the real core against a loopback fake Ollama
swift run Aide        # needs `ollama serve`; ⌘⌃L on selected text in any app; menu bar ✦ → 打开 Aide
```

First use asks for **Accessibility** permission (System Settings → Privacy & Security). When running
from a terminal, macOS attributes the permission to that terminal app until Aide ships as a signed `.app`.

Env: `AIDE_REPO` (source tree, defaults to this checkout), `AIDE_PYTHON`, `AIDE_DATA_DIR`,
`AIDE_OLLAMA_URL`. Styling uses system fonts and semantic colors only; brand styling stays out of the repo.
