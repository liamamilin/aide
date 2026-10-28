<h1 align="center">Aide</h1>
<p align="center"><em>A native macOS entry point to a local AI: press <code>⌘⌃L</code>, say what you want — or select text first — and Aide does it. Local Ollama models, nothing uploaded.</em></p>

<p align="center"><a href="README.zh.md">中文</a> | <strong>English</strong></p>

[![macOS](https://img.shields.io/badge/platform-macOS%2014%2B-lightgrey.svg)]()
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.com)
[![Swift](https://img.shields.io/badge/UI-SwiftUI%20%2F%20AppKit-blue.svg)]()

---

## Aide (native app)

The native app is a SwiftUI/AppKit shell (`macos/`) around the same headless Python core (`ai_desktop/core`) used by the legacy Qt app. It is Apple-silicon only for now.

| What you do | What happens |
|---|---|
| Press `⌘⌃L` and type a request ("打开备忘录", "找最近修改的 PDF", "查看备忘录有多少条") | The **Quick Panel** runs it with the local desktop tools; found apps and files show up as items you can open or reveal in Finder |
| Select text, then `⌘⌃L` | Pick an action (translate, explain, summarize, rewrite, or your own — number keys, arrows, Enter) or type your own instruction; `⌘↩` continues in the Workspace, `↩` copies and returns to your app |
| Press `⌘⌃S`, drag a region | A new Workspace chat opens with the screenshot attached (needs a vision-capable model) |
| Open **Workspace** from the menu bar | Conversation history and search, streaming Markdown answers (tables, lists, code with copy), regenerate, edit your last question, agent and model switching |
| Ask Aide to change something (delete a note or a reminder, append to a note, …) | A confirmation shows the exact action first; nothing runs without your approval |

Settings (menu bar → 设置…, `⌘,`): Ollama address and thinking mode, a recordable Quick Panel hotkey, custom agents and custom quick actions; sampling parameters are under "高级".

**Not in the native app yet:** the desktop pet, web search and the Bash tool. These stay in the legacy Qt app (below).

### Install

1. Download `Aide-<version>-arm64.dmg` from [Releases](https://github.com/liamamilin/aide/releases) and drag `Aide` into Applications.
2. The build is **not notarized** (no Developer ID certificate yet): open it once, then System Settings → Privacy & Security → "Open Anyway", or `xattr -dr com.apple.quarantine /Applications/Aide.app`.
3. Install [Ollama](https://ollama.com), run `ollama serve`, and pull a model (`ollama pull qwen3:14b`).
4. Allow **Accessibility** (reads the selected text), **Screen Recording** (if macOS asks on the first `⌘⌃S`) and, when you ask for them, **Automation** for Notes and Reminders.

Signing: a local build is signed with a stable local identity when one is in your keychain (so permission grants survive rebuilds), otherwise ad-hoc. Release DMGs are ad-hoc signed and not notarized.

### Build from source

```bash
pip install -r requirements-dev.txt && pip install -e .
./scripts/build_native.sh --smoke      # dist/Aide.app and dist/Aide-<version>-arm64.dmg
cd macos && swift test                  # Swift tests (spawns the real core against a fake Ollama)
```

Details: [macos/README.md](macos/README.md). Your history is shared with the legacy app (`~/Library/Application Support/ai-desktop-assistant/`).

---

## Legacy Qt app

The original PyQt5 app (floating button, desktop pet, web search, Bash tool) still works and is described below.

## Three Ways to Open

| Entrance | Interaction |
|----------|-------------|
| **Hotkey** `⌘⌃L` | Select text in any app → press the hotkey → text is auto-filled into the dialog |
| **Floating button** | Circular icon on the right edge of the screen; left-click to toggle the dialog, right-click for the menu |
| **Menu bar icon** | Persistent in the macOS menu bar; left-click to toggle the dialog, right-click to switch agents quickly or restore a hidden pet |

---

## Installation

### Option 1: DMG Install (Recommended)

1. Download the latest `AI桌面助手-*.dmg` from [Releases](https://github.com/liamamilin/aide/releases)
2. Double-click the DMG and drag `AI桌面助手` into Applications
3. On first launch, allow it to run in System Settings → Privacy & Security
4. Make sure [Ollama](https://ollama.com) is installed and running

### Option 2: From Source

**Prerequisites**: [Ollama](https://ollama.com) installed with a model pulled:

```bash
ollama serve
ollama pull qwen3:14b
```

**Install**:

```bash
cd /path/to/AI桌面助手
pip install -e .
```

**Development** (required to run tests):

```bash
pip install -r requirements-dev.txt
```

**Run**:

```bash
aide
```

**Build the `.app` bundle** (requires PyInstaller):

```bash
# Quick build (clean → build → sign)
./scripts/build.sh

# Run ruff + pytest before building
./scripts/build.sh --test

# Build + smoke test
./scripts/build.sh --smoke

# Build + generate DMG
./scripts/build.sh --dmg

# Everything
./scripts/build.sh --test --smoke --dmg
```

`ai_desktop/version.py` is the authoritative version source for Python packaging,
the running app, `Info.plist`, and DMG names. Builds prefer an available stable
code-signing identity (Developer ID, Apple Development, or the project's local
`AI Desktop Assistant` certificate) and fall back to ad-hoc only when none is
available. Set `AIDE_SIGN_IDENTITY` to choose an identity; use `-` to force
ad-hoc. Stable signing keeps macOS permissions attached to the same app across
rebuilds. The read-only preflight never commits, tags, or pushes:

```bash
python scripts/release_check.py --version 1.5.0 --require-new-tag
```

---

## Features

### Chat
- **Streaming output** — token-level real-time rendering, batched at 50ms
- **Thinking process** — LLM reasoning shown live, collapsed into `💭 Thinking` when complete
- **Markdown rendering** — code blocks, lists, bold, headings; headings use the accent blue
- **Multi-turn conversation** — follow-ups and corrections, persisted in SQLite
- **Safe selection capture** — preserves enumerable text, rich-text, and image clipboard formats, and never overwrites a newer user copy
- **Quick actions** — after selecting text, run Translate, Explain, Summarize, or Rewrite with number keys, arrows, and Enter
- **Image understanding (multimodal)** — validated managed attachments, missing-file recovery, and visible model capability checks for paste, drag-and-drop, 📎 attach, or `⌘⌃S` region screenshot
- **On-device OCR** — extract text from any pending image with Apple Vision, edit or copy the result, then choose text only or text plus the original image before sending
- **Interrupt ⏹** — stop streaming generation at any time
- **Edit ✏️** — hover a user message for the edit button; click to refill the input and resend
- **Copy 📋** — hover an assistant reply for the copy button
- **Auto-growing input** — height grows from 36px to 120px on multi-line input, scrollable when long
- **Export** — copy the whole conversation as Markdown in one click
- **Notifications** — macOS notification when a reply finishes while the window is in the background

### Agent
- **Tool tasks (candidate)** — enable Bash / Parallel / Exa for any conversation agent; saved tool choices carry across new conversations, agents, and app restarts. Tool runs follow the selected model after a fresh capability check, with command approval, cancellation, citations, and run history. See the [tool settings guide](docs/工具设置继承-2026-10-07.md).
- **5 built-in agents**: Code Expert 💻 / Translator 🌐 / General Assistant 🤖 / Summarizer 📄 / Polisher ✍️
- **Custom agents** — create / edit / delete with custom emoji icon and prompt
- **Task model profiles** — assign a reusable model, thinking mode, temperature, and output limit to each agent; every field can inherit the global setting
- **Quick switch from menu bar** — switch agents directly from the menu bar icon's right-click menu

### History
- **Browse conversations** — stable pagination reaches all conversations and shows message counts
- **Full-text search** — search conversation titles and message contents, 300ms debounce
- **Rename / Delete / Load** — edit validated titles, load a conversation, or delete it

### Settings
- **Runtime configuration** — right-click the floating button → Settings… → change Ollama URL, timeout, context window, hotkeys
- **Persistence** — settings, agents, model selection, chat geometry, and floating-button placement survive restarts and recover onto an available display
- **Auto-restore** — loads the last conversation on startup, keeping your selected agent

### Database upgrades and recovery

Before upgrading an existing unversioned database, the app creates a consistent
SQLite backup in the data directory's `backups/` folder. Schema changes commit as
one transaction; a failed migration leaves the old schema and data intact. Stop
the app before restoring a matching older backup:

```bash
python scripts/restore_database.py "/path/to/backup.sqlite3"
```

The restore utility first keeps the current database as a `before-restore`
safety backup. Starting a newer app after restoring an older schema upgrades it
again, so use the application version that matches the restored backup when
downgrading.

---

## Configuration

Change at runtime via the **Settings panel**, or edit `ai_desktop/config.py`:

| Key | Default | Description |
|-----|---------|-------------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-uncensored:9b` | Default model (falls back to the first available model if missing) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama service URL |
| `HOTKEY` | `<cmd>+<ctrl>+l` | Global hotkey |
| `OLLAMA_NUM_PREDICT` | `20480` | Max output tokens |
| `OLLAMA_NUM_CTX` | `8192` | Context window |
| `OLLAMA_TEMPERATURE` | `0.7` | Generation temperature (0.0 ~ 2.0) |
| `OLLAMA_TOP_P` | `0.9` | Nucleus sampling threshold |
| `OLLAMA_TOP_K` | `40` | Top-K sampling |
| `OLLAMA_REPEAT_PENALTY` | `1.1` | Repetition penalty |
| `OLLAMA_THINK` | `True` | Model thinking / reasoning toggle |
| `OLLAMA_MAX_ROUNDS` | `10` | Max conversation rounds kept |
| `OLLAMA_KEEP_ALIVE` | `30m` | Model keep-alive duration |
| `OLLAMA_TIMEOUT` | `120` | HTTP timeout (seconds) |
| `SCREENSHOT_HOTKEY` | `<cmd>+<ctrl>+s` | Region screenshot sent to the conversation (requires Screen Recording permission) |

Hotkey syntax: `<cmd>`, `<shift>`, `<ctrl>`, `<alt>`. Do not use `<fn>` or `<option>`.

---

## Troubleshooting

### Dialog opens but the input is empty

Logs contain `This process is not trusted!` or `Clipboard unchanged after Cmd+C`:

> macOS Accessibility permission not granted.

**Fix**: System Settings → Privacy & Security → Accessibility → enable your terminal app; if needed, uncheck and re-check to refresh the cache.

### No response after launch

Check Ollama: `curl http://localhost:11434/api/tags`, or watch the status dot 🔴 → 🟢 next to the input bar.

### Screenshot fails or nothing happens

Clicking 📎 → Screenshot or pressing `⌘⌃S` shows "Screenshot failed" / nothing happens:

> macOS Screen Recording permission not granted.

**Fix**: System Settings → Privacy & Security → Screen Recording → enable "AI 桌面助手" (or "Terminal" in dev mode), then restart the app. The app pops up a guide when the permission is missing and can jump straight to the settings page.

### Model doesn't understand image content after sending

The current model doesn't support vision (multimodal). **Fix**: switch to a vision model (e.g. `llava`, `qwen2.5vl`) in the dropdown and resend.

---

## Project Structure

```
ai_desktop/
├── main.py                     # Entry point + ChatController
├── config.py                   # LLM / hotkey / agent config
├── __main__.py                 # python -m ai_desktop support
├── version.py                  # authoritative application version
├── settings_manager.py         # Persisted config load / apply
├── agent_manager.py            # Agent list management / switch / save
├── capture/
│   ├── hotkey_listener.py      # pynput global hotkeys (dev mode)
│   ├── nsevent_monitor.py      # NSEvent global listener (frozen mode)
│   ├── screenshot.py           # screencapture region capture (permission error detection)
│   ├── clipboard_monitor.py    # ⌘C simulate → read → restore (dual fallback)
│   └── text_normalizer.py      # text cleaning + truncation
├── llm/
│   └── chat_client.py          # Ollama /api/chat (streaming + thinking + multimodal images)
├── services/
│   └── ocr_service.py          # Apple Vision OCR + cancellable background tasks + layout assembly
├── ui/
│   ├── float_button.py         # floating circular button (drag / cross-screen / right-click)
│   ├── menubar_icon.py         # macOS menu bar icon + agent menu
│   ├── chat_dialog.py          # multi-turn chat + hotkeys + copy/edit/interrupt + image send/receive
│   ├── ocr_preview_dialog.py   # editable OCR preview + explicit image retention choice
│   ├── history_dialog.py       # history browsing + full-text search
│   ├── agent_editor.py         # agent management (add/edit/delete + emoji picker)
│   ├── settings_dialog.py      # runtime settings panel
│   ├── markdown.py             # Markdown → HTML
│   ├── styles.py               # centralized QSS constants (48, lazy-loaded)
│   ├── theme.py                # explicit light/dark ColorSet values
│   ├── frameless_mixin.py      # FramelessDragMixin + TitleBar
│   └── __init__.py
├── utils/
│   ├── images.py               # image storage / base64 / type detection
│   ├── logging.py              # logging config
│   ├── storage.py              # SQLite persistence (conversations/messages/settings/agents/image paths)
│   └── permissions.py          # Accessibility + Input Monitoring permission check/request
scripts/
├── aide.spec                   # shared local/CI PyInstaller definition
├── build.sh                    # build, sign, validate, smoke, package
├── build_dmg.sh                # versioned DMG creation
├── release_check.py            # read-only source/bundle preflight
└── smoke_test.sh               # isolated packaged-app launch
tests/
├── conftest.py                 # shared fixtures (qapp, storage, mocks)
├── fake_ollama.py              # local HTTP test server
└── test_*.py                   # UI, lifecycle, transport, data, and release tests
```

---

## Testing

The automated suite covers UI state, request/session lifecycles, cancellable
transport, capture, persistence, theming, entry points, and release packaging:

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run the same checks used by CI
python -m ruff check ai_desktop/ tests/ scripts/release_check.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

Tests use `pytest-qt` for real PyQt5 window rendering; external dependencies (Ollama HTTP, clipboard, macOS ctypes, screencapture) are isolated via mocks.
