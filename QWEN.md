# QWEN.md — AI 桌面助手 (AI Desktop Assistant)

> Auto-generated project context for Qwen Code. Keep this file up to date as the project evolves.

## Overview

A macOS desktop AI assistant with three entry points:
1. **Floating button** — circular draggable icon, cross-Space, cross-screen
2. **Menu bar icon** — macOS NSStatusBar icon with Agent switcher menu
3. **Global hotkey** (`⌘⌃L`) — captures selected text from any app

Captured text is sent to a local LLM (Ollama) via a streaming multi-turn chat window. Features 3+ custom Agents with `thinking` process visualization and Markdown rendering. Conversations are persisted in SQLite with full-text search.

**v1 Immutable Constraints:**
1. App-agnostic — works in Terminal, VS Code, browser, PDF, any macOS app
2. "Selection" as the intent entry point (no manual copy-paste)
3. Local LLM only (Ollama), no data ever uploaded
4. Minimal interaction: one step to the input box, Enter to send

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python ≥ 3.10 |
| UI | PyQt5 (Qt 5) |
| Global Hotkey | pynput (`keyboard.GlobalHotKeys`) |
| Clipboard | pynput Controller + `pbpaste`/`pbcopy` (macOS) |
| LLM Backend | Ollama `/api/chat` (streaming with `thinking` tokens) |
| HTTP | `requests` (with `stream=True` for SSE) |
| Persistence | SQLite via `sqlite3` (WAL mode, thread-local connections) |
| Markdown | Custom regex-based renderer (no external deps) |
| Search | SQL LIKE query (titles + message content) |

## Project Structure

```
ai_desktop/
├── main.py                      # Entry point + ChatController (orchestrator)
├── config.py                    # LLM/Hotkey/Agent config (module constants + dataclasses)
├── settings_manager.py          # Runtime settings persistence/apply
├── agent_manager.py             # Built-in/custom Agent merge and switching
├── __main__.py                  # python -m ai_desktop support
├── capture/
│   ├── hotkey_listener.py       # pynput GlobalHotKeys wrapper + runtime reregister
│   ├── clipboard_monitor.py     # ⌘C simulation (pynput → osascript fallback)
│   └── text_normalizer.py       # Whitespace normalization + truncation (12K char limit)
├── llm/
│   └── chat_client.py           # ChatClient (non-streaming) + ChatStream (streaming iterator)
├── ui/
│   ├── float_button.py          # Circular draggable button, cross-Space/cross-screen, context menu
│   ├── menubar_icon.py          # macOS menu bar QSystemTrayIcon with Agent switcher
│   ├── chat_dialog.py           # Frameless multi-turn chat window with streaming + shortcuts
│   ├── history_dialog.py        # Conversation history browser with search
│   ├── agent_editor.py          # Agent management (add/edit/delete) with emoji picker
│   ├── settings_dialog.py       # Runtime config panel (URL, timeout, tokens, hotkey)
│   ├── markdown.py              # Markdown → inline HTML (headings, code blocks, bold, lists)
│   └── styles.py                # Shared QSS string constants
└── utils/
    ├── logging.py               # stderr logging setup, suppresses pynput/urllib3 noise
    └── storage.py               # SQLite CRUD: conversations, messages, settings, custom agents
tests/
├── test_smoke.py                # Smoke tests for text_normalizer + markdown renderer
├── test_storage.py              # DB CRUD tests (temp SQLite)
├── test_chat_client.py          # ChatStream parsing tests (mocked HTTP)
├── test_settings_manager.py     # Settings persistence/apply tests
├── test_agent_manager.py        # Agent merge/switch/custom tests
└── test_*_dialog.py             # pytest-qt UI tests
```

## Architecture & Data Flow

```
User selects text → ⌘⌃L (pynput background thread)
    │
    ▼
clipboard_monitor.read_selection()
  ├── Save clipboard → simulate ⌘C (pynput CGEvent, osascript fallback)
  └── text_normalizer.normalize()
    │
    ▼ (signal bridge: pynput thread → Qt main thread via pyqtSignal, 100ms delay)
ChatController._on_hotkey_triggered()
  └── Open / show ChatDialog, paste text into input
    │
    ▼ (user presses Enter)
ChatController._on_user_message()
  ├── Guard: skip if worker already running (restores text + shows busy hint)
  ├── Save to SQLite (create_conversation + save_message, auto-title from first msg)
  ├── StreamingChatWorker (QThread) starts (interruptible via ⏹ button)
  │     └── ChatClient.chat_stream() → yields (kind, token) tuples
  │           kind ∈ {"thinking", "response", "error"}
  └── UI updates via signals:
        thinking_chunk → append_thinking_chunk (buffered)
        chunk          → append_stream_chunk (buffered)
        done           → finalize_assistant_stream (thinking folded as <details>)
                      → macOS notification if dialog in background
    │
    ▼
50ms QTimer flush: buffers accumulated tokens → QLabel (PlainText during stream)
  └── On done: convert to Markdown HTML (RichText) + connect copy button
```

## Three Entry Points

| Entry | Interaction | Notes |
|-------|-----------|-------|
| **Hotkey** `⌘⌃L` | Select text → press → dialog opens with text pasted | Configurable at runtime via Settings |
| **Float button** | Left-click: toggle dialog. Right-click: context menu | Cross-Space via `NSWindowCollectionBehaviorCanJoinAllSpaces` |
| **Menu bar icon** | Left-click: toggle dialog. Menu: Agent switch / Settings / Exit | QSystemTrayIcon, macOS native |

## Thread Safety Pattern

```
pynput callback (background thread)
    │
    ▼ pyqtSignal.emit()
Qt main thread slot
    │
    ▼
Safe UI operations
```

- `HotkeyListener._on_global_hotkey()` runs on pynput's thread — must NOT touch UI directly
- It emits `_hotkey_triggered` signal; `_on_hotkey_triggered` slot runs on main thread
- `StreamingChatWorker` is a QThread subclass, communicates only via signals
- Clipboard save/restore happens on the pynput thread (no Qt dependency)
- Stream buffer access: written by signal slots, read by QTimer callback — both on main thread (no race)

## Key Conventions

### Code Style
- **Python typing**: Uses `list[Type]`, `dict`, `|` union syntax (Python 3.10+)
- **Comments**: Chinese docstrings on modules; inline comments in Chinese; section dividers use `──` and `══` separators
- **Naming**: `snake_case` for functions/variables; `CamelCase` for classes; private members prefixed with `_`
- **Config**: Module-level `UPPER_CASE` constants + `@dataclass` for structured config (Agent, Message, Conversation)

### PyQt5 Signal/Slot Safety
- **Lambda + clicked(bool)**: Always accept `checked` as first param: `lambda checked, t=value: handler(t)`
- **Exception guard**: Any slot that could throw must wrap in try/except to prevent `qFatal()` → `abort()`
- **Clipboard access**: Always wrapped in try/except (PyQt5 may abort on clipboard errors)

### UI Patterns
- Frameless windows (`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`) with custom title bars
- `QLabel`-based chat bubbles (blue right-aligned for user, gray left-aligned for assistant)
- 50ms batch timer for streaming display to avoid UI stutter
- `show_near(anchor)` pattern for positioning dialog relative to float button
- Auto-hide on focus loss (`changeEvent` → `ActivationChange`)
- Stretch-before-insert layout pattern: `_msg_layout.insertWidget(count-1, widget)`
- Copy button on assistant bubbles: hover-visible `📋`, connected after stream completes

### Streaming
- Thinking/response tokens interleaved; `ChatStream.__iter__` demuxes them
- During stream: plain text with `💭` prefix for thinking
- After stream: thinking folded into `<details><summary>💭 思考过程</summary>...</details>`, response as Markdown HTML
- Buffer-then-flush pattern: 50ms flush timer

### Selection Capture
- Two-tier: pynput CGEvent (main) → osascript System Events (fallback)
- Does NOT use macOS Accessibility API (unreliable for Electron/Chromium apps)
- Saves clipboard → simulates `⌘C` → waits 80ms → reads `pbpaste` → restores clipboard
- 100ms pre-delay via QTimer to avoid modifier key conflict with the hotkey

### Persistence
- SQLite with WAL journal mode, foreign keys ON
- Thread-local connections (one per thread via `threading.local()`)
- Conversation title auto-derived from first user message (first 40 chars)
- Custom agents stored as JSON in `settings` table
- DB file: `chat_history.db` in project root

## Building, Running & Testing

```bash
# Install (editable)
pip install -e .

# Run
aide

# Or directly
python -m ai_desktop

# Run all tests
pytest tests/ -v
```

**Prerequisites:**
- Ollama must be running (`ollama serve`)
- Model must be pulled (configure in Settings or `config.py`)
- macOS only (uses `pbpaste`/`pbcopy`, `NSWindow` Objective-C bridging, QSystemTrayIcon)

## Configuration

Configurable at runtime via **Settings** (float button right-click → 设置…) or directly in `ai_desktop/config.py`:

| Setting | Default | Notes |
|---------|---------|-------|
| `OLLAMA_MODEL` | `sorc/qwen3.5-instruct-uncensored:9b` | Must exist locally (`ollama list`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Changeable at runtime |
| `HOTKEY` | `<cmd>+<ctrl>+l` | Runtime changeable via Settings |
| `OLLAMA_NUM_PREDICT` | 20480 | Max output tokens |
| `OLLAMA_NUM_CTX` | 8192 | Context window size |
| `OLLAMA_TEMPERATURE` | 0.7 | Generation temperature (0.0~2.0) |
| `OLLAMA_TOP_P` | 0.9 | Nucleus sampling threshold (0.0~1.0) |
| `OLLAMA_TOP_K` | 40 | Top-K sampling |
| `OLLAMA_REPEAT_PENALTY` | 1.1 | Repetition penalty coefficient |
| `OLLAMA_KEEP_ALIVE` | `30m` | Model stays loaded in memory |
| `OLLAMA_TIMEOUT` | 120 | HTTP request timeout (seconds) |
| `MAX_TEXT_LENGTH` | 12000 | Input truncation limit |

All settings are persisted in SQLite and survive restarts.

**pynput key names on macOS:** Use `<alt>` not `<option>`; use `<cmd>` not `<command>`. `<fn>` and `<option>` are rejected by `HotKey.parse`.

## Agents

Defined as `Agent` dataclasses. Built-in agents in `config.py`. Custom agents created/edited/deleted via **Agent Manager** (⚙ button in chat title bar or float button → 设置… → 管理 Agent). Custom agents stored as JSON in `settings` table, loaded on startup and merged with built-in.

| Agent | ID | Icon | Behavior |
|-------|-----|------|----------|
| 💻 Code Expert | `code_expert` | 💻 | Code review, debugging, writing; structured output |
| 🌐 Translator | `translator` | 🌐 | CN↔EN translation; direct output |
| 🤖 General | `general_assistant` | 🤖 | Catch-all; concise Chinese responses |
| 📄 Summarizer | `summarizer` | 📄 | 1-3 sentence summary, ≤100 chars, Chinese output |
| ✍️ Polisher | `polisher` | ✍️ | Improve wording & grammar, keep original meaning |
| *(custom)* | `custom_N` | user-defined | User-defined via Agent Manager |

## Memory System Notes

The project has associated memory files at `~/.qwen/projects/-Users-milin-2026-AI----/memory/`:
- `project_v1_scope.md` — v1 constraints and scope boundaries
- `reference_macos_accessibility_api.md` — Why AXSelectedText was dropped
- `user_design_approach.md` — User's design philosophy
- `feedback_plan_before_code.md` — Structured plan before implementation
- `reference_pynput_key_names_macos.md` — pynput key name quirks on macOS
- `reference_pynput_pyqt5_macos.md` — Thread safety: pynput callbacks → Qt signals
- `reference_pynput_modifier_conflict.md` — Controller+Listener share CGEventTap
- `reference_ime_editing_state_hotkey.md` — IME interference hypothesis
- `reference_pyqt5_slot_gotchas.md` — qFatal on unhandled exceptions + clicked(bool)

## Edge Cases & Gotchas

- **Empty selection / no change**: If clipboard content unchanged after ⌘C, `read_selection()` returns `None` and no window is opened
- **Ollama not running**: Warning logged on startup + red dot in input bar; chat will fail gracefully
- **Model not pulled**: Will appear in model list (fallback to config default) but API calls will fail
- **Concurrent sends**: `_on_user_message` guards against multiple workers; restores text + shows `⏳ 等待回复完成...` hint
- **Window auto-hide**: Dialog hides on focus loss (clicking outside) — by design; toggle via float button menu
- **Cross-Space**: Float button uses `NSWindowCollectionBehaviorCanJoinAllSpaces` (value 1) via `objc_msgSend`
- **Screen tracking**: 500ms timer checks if cursor moved to a different screen and repositions the float button
- **Accessibility permissions (macOS)**: pynput Controller requires Accessibility permission. Two-tier fallback: pynput → osascript, both need the same permission.
- **Esc behavior**: Two-stage — first Esc clears input, second Esc closes dialog
- **`init_db()`**: Called at `ChatController.__init__` (before any DB query), idempotent
- **Copy button**: Connected after stream completes with disconnect guard + try/except on clipboard access

## Development Roadmap

Final status as of 2026-07-01:

| Tier | Status | Items |
|------|--------|-------|
| P0 | ✅ 3/3 | Model combo, input auto-focus, copy button |
| P1 | ✅ 4/4 | History browser ✅, Ollama status ✅, keyboard shortcuts ✅, ~~dark mode~~ |
| P2 | ✅ 4/4 | Settings UI, custom agents, export, session restore |
| P3 | ⚡ 3/4 | Menu bar icon ✅, search ✅, UI/backend tests ✅, ~~image input~~ |
| Post | ✅ | Interrupt button ⏹, edit button ✏️, macOS notifications, async HTTP worker, slot error guards, agent persistence fix |
| Quality | ✅ | 110 automated tests covering storage, LLM parsing, managers, UI signals/state, clipboard, hotkeys |
| | **13/14 + enhancements** | **Project complete; productization remains** |
