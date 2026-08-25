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
| **Menu bar icon** | Persistent in the macOS menu bar; left-click to toggle the dialog, right-click to switch agents quickly |

---

## Installation

### Option 1: DMG Install (Recommended)

1. Download the latest `AI桌面助手-v*.dmg` from [Releases](https://github.com/liamamilin/aide/releases)
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

---

## Features

### Chat
- **Streaming output** — token-level real-time rendering, batched at 50ms
- **Thinking process** — LLM reasoning shown live, collapsed into `💭 Thinking` when complete
- **Markdown rendering** — code blocks, lists, bold, headings; headings use the accent blue
- **Multi-turn conversation** — follow-ups and corrections, persisted in SQLite
- **Image understanding (multimodal)** — send images by pasting, drag-and-drop, 📎 attach, or `⌘⌃S` region screenshot; images persist with the conversation and can be clicked for a full view. Select a vision-capable multimodal model (e.g. `llava`, `qwen2.5vl`) in the model dropdown; images are processed locally only
- **Interrupt ⏹** — stop streaming generation at any time
- **Edit ✏️** — hover a user message for the edit button; click to refill the input and resend
- **Copy 📋** — hover an assistant reply for the copy button
- **Auto-growing input** — height grows from 36px to 120px on multi-line input, scrollable when long
- **Export** — copy the whole conversation as Markdown in one click
- **Notifications** — macOS notification when a reply finishes while the window is in the background

### Agent
- **5 built-in agents**: Code Expert 💻 / Translator 🌐 / General Assistant 🤖 / Summarizer 📄 / Polisher ✍️
- **Custom agents** — create / edit / delete with custom emoji icon and prompt
- **Quick switch from menu bar** — switch agents directly from the menu bar icon's right-click menu

### History
- **Browse conversations** — open the history window to see all conversations with message counts
- **Full-text search** — search conversation titles and message contents, 300ms debounce
- **Delete / Load** — load a conversation from history, or delete the ones you don't need

### Settings
- **Runtime configuration** — right-click the floating button → Settings… → change Ollama URL, timeout, context window, hotkeys
- **Persistence** — all settings, agents, and model selection survive restarts
- **Auto-restore** — loads the last conversation on startup, keeping your selected agent

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
├── ui/
│   ├── float_button.py         # floating circular button (drag / cross-screen / right-click)
│   ├── menubar_icon.py         # macOS menu bar icon + agent menu
│   ├── chat_dialog.py          # multi-turn chat + hotkeys + copy/edit/interrupt + image send/receive
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
└── scripts/
    └── aide.spec               # PyInstaller packaging config
tests/
├── conftest.py                 # shared fixtures (qapp, mocker)
├── test_smoke.py               # text normalization + Markdown rendering (5)
├── test_storage.py             # DB CRUD + image round-trip (23)
├── test_chat_client.py         # stream parsing + multimodal message building (12)
├── test_main_entry.py          # python -m entry point (1)
├── test_settings_manager.py    # config load/apply/validate (8)
├── test_agent_manager.py       # agent init/switch/save (10)
├── test_chat_dialog.py         # chat window signals/state/image attachments (42)
├── test_settings_dialog.py     # settings panel validation/signals (6)
├── test_history_dialog.py      # history load/search/delete (6)
├── test_agent_editor.py        # agent editor CRUD + add-dialog (10)
├── test_float_button.py        # floating button menu/signals (8)
├── test_menubar_icon.py        # menu bar agent menu/signals (9)
├── test_images.py              # image storage/base64 (5)
├── test_screenshot.py          # screenshot success/cancel/permission error (4)
├── test_menu_contrast.py       # menu selected-item contrast (4)
├── test_theme.py               # light/dark ColorSet contrast (8)
├── test_frameless_mixin.py     # drag / frameless (2)
├── test_clipboard_monitor.py   # clipboard capture (2)
├── test_hotkey_listener.py     # hotkey parsing/registration (3)
├── test_nsevent_monitor.py     # NSEvent backend (7)
├── test_permissions.py         # permission detection (4)
├── test_update_checker.py      # update checking (9)
└── test_crash_handler.py       # crash diagnostics (2)
```

---

## Testing

The project has **190 automated tests** covering UI signals/state, the data layer, utilities, and the entry point:

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v    # 190 tests

# By category
pytest tests/test_chat_dialog.py -v        # chat window (42)
pytest tests/test_storage.py -v            # database (23)
pytest tests/test_chat_client.py -v        # LLM stream parsing + multimodal (12)
pytest tests/test_agent_manager.py -v      # agent management (10)
pytest tests/test_agent_editor.py -v       # agent editor + add dialog (10)
pytest tests/test_settings_manager.py -v   # config management (8)
pytest tests/test_settings_dialog.py -v    # settings panel (6)
pytest tests/test_history_dialog.py -v     # history browsing (6)
pytest tests/test_float_button.py -v       # floating button (8)
pytest tests/test_menubar_icon.py -v       # menu bar icon (9)
pytest tests/test_images.py -v             # image storage/base64 (5)
pytest tests/test_screenshot.py -v         # screenshot success/cancel/permission error (4)
pytest tests/test_theme.py -v              # light/dark contrast (8)
pytest tests/test_main_entry.py -v         # entry point (1)
pytest tests/test_smoke.py -v              # utilities (5)
```

Tests use `pytest-qt` for real PyQt5 window rendering; external dependencies (Ollama HTTP, clipboard, macOS ctypes, screencapture) are isolated via mocks.