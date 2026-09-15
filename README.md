<h1 align="center">AI Desktop Assistant</h1>
<p align="center"><em>Always-on AI assistant for macOS — select text → <code>⌘⌃L</code> → ask in one keystroke, or press <code>⌘⌃S</code> to capture a screenshot region and ask. Local LLM, zero uploads.</em></p>

<p align="center"><a href="README.zh.md">中文</a> | <strong>English</strong></p>

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/UI-PyQt5-green.svg)](https://pypi.org/project/PyQt5/)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.com)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)]()

---

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
