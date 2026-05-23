# QWEN.md — AI 桌面助手 (AI Desktop Assistant)

> Auto-generated project context for Qwen Code. Keep this file up to date as the project evolves.

## Overview

A macOS desktop AI assistant — a floating button + global hotkey (`⌘⌃L`) that captures selected text from any application and sends it to a local LLM (Ollama) via a streaming multi-turn chat window. Supports 3 switchable Agents (Code Expert, Translator, General Assistant) with `thinking` process visualization and Markdown rendering.

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
| Clipboard | pyperclip + `pbpaste`/`pbcopy` (macOS) |
| LLM Backend | Ollama `/api/chat` (streaming with `thinking` tokens) |
| HTTP | `requests` (with `stream=True` for SSE) |
| Persistence | SQLite via `sqlite3` (WAL mode, thread-local connections) |
| Markdown | Custom regex-based renderer (no external deps) |

## Project Structure

```
ai_desktop/
├── main.py                    # Entry point + ChatController (orchestrator)
├── config.py                  # LLM/Hotkey/Agent config (module constants + dataclasses)
├── capture/
│   ├── hotkey_listener.py     # pynput GlobalHotKeys wrapper
│   ├── clipboard_monitor.py   # ⌘C simulation → read clipboard → restore clipboard
│   └── text_normalizer.py     # Whitespace normalization + truncation (12K char limit)
├── llm/
│   └── chat_client.py         # ChatClient (non-streaming) + ChatStream (streaming iterator)
├── ui/
│   ├── float_button.py        # Circular draggable button, cross-Space/cross-screen tracking
│   ├── chat_dialog.py         # Frameless multi-turn chat window with streaming display
│   ├── markdown.py            # Markdown → inline HTML (headings, code blocks, bold, lists)
│   └── styles.py              # Shared QSS string constants
└── utils/
    ├── logging.py             # stderr logging setup, suppresses pynput/urllib3 noise
    └── storage.py             # SQLite CRUD: conversations + messages (thread-local conn)
tests/
└── test_smoke.py              # Smoke tests for text_normalizer + markdown renderer
```

## Architecture & Data Flow

```
User selects text → ⌘⌃L (pynput background thread)
    │
    ▼
clipboard_monitor.read_selection()
  ├── Save clipboard → simulate ⌘C (pynput CGEvent) → read clipboard → restore clipboard
  └── text_normalizer.normalize()
    │
    ▼ (signal bridge: pynput thread → Qt main thread via pyqtSignal)
ChatController._on_hotkey_triggered()
  └── Open ChatDialog, paste text into input, focuses input
    │
    ▼ (user presses Enter)
ChatController._on_user_message()
  ├── Save to SQLite (create_conversation + save_message)
  ├── StreamingChatWorker (QThread) starts
  │     └── ChatClient.chat_stream() → yields (kind, token) tuples
  │           kind ∈ {"thinking", "response", "error"}
  └── UI updates via signals:
        thinking_chunk → append_thinking_chunk (buffered)
        chunk          → append_stream_chunk (buffered)
        done           → finalize_assistant_stream (thinking folded as <details>)
    │
    ▼
50ms QTimer flush: buffers accumulated tokens → QLabel (PlainText during stream)
  └── On done: convert to Markdown HTML (RichText) with folded thinking block
```

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

## Key Conventions

### Code Style
- **Python typing**: Uses `Optional`, `list[Type]`, `|` union syntax throughout
- **Comments**: Chinese docstrings on modules; inline comments in Chinese; section dividers use `──` and `══` separators
- **Naming**: `snake_case` for functions/variables; `CamelCase` for classes; private members prefixed with `_`
- **Config**: Module-level `UPPER_CASE` constants + `@dataclass` for structured config (Agent, Message, Conversation)

### UI Patterns
- Frameless windows (`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`) with custom title bars
- `QLabel`-based chat bubbles (blue right-aligned for user, gray left-aligned for assistant)
- 50ms batch timer for streaming display to avoid UI stutter
- `show_near(anchor)` pattern for positioning dialog relative to float button
- Auto-hide on focus loss (`changeEvent` → `ActivationChange`)
- Stretch-before-insert layout pattern: `_msg_layout.insertWidget(count-1, widget)` (last item is always a stretch spacer)

### Streaming
- Thinking tokens and response tokens are interleaved in the Ollama stream; `ChatStream.__iter__` demuxes them
- During stream: displayed as plain text with `💭` prefix for thinking
- After stream: thinking folded into `<details><summary>💭 思考过程</summary>...</details>`, response body rendered as Markdown HTML
- Buffer-then-flush pattern: tokens accumulate in `_stream_buffer`/`_thinking_buffer`, flushed every 50ms

### Selection Capture
- Does NOT use macOS Accessibility API (unreliable for Electron/Chromium apps)
- Instead: saves clipboard → simulates `⌘C` via pynput CGEvent → waits 150ms → reads `pbpaste` → restores clipboard
- 50ms pre-delay to avoid modifier key conflict with the hotkey itself

### Persistence
- SQLite with WAL journal mode, foreign keys ON
- Thread-local connections (one per thread via `threading.local()`)
- Conversation title auto-derived from first user message (first 40 chars)
- DB file: `chat_history.db` in project root (not in `ai_desktop/`)

## Building, Running & Testing

```bash
# Install (editable)
pip install -e .

# Run
ai-desktop

# Or directly
python -m ai_desktop.main

# Run smoke tests
pytest tests/
# or
python tests/test_smoke.py
```

**Prerequisites:**
- Ollama must be running (`ollama serve`)
- Model must be pulled (`ollama pull qwen3:14b` or configure in `config.py`)
- macOS only (uses `pbpaste`/`pbcopy`, `NSWindow` Objective-C bridging)

## Configuration

Edit `ai_desktop/config.py`:

| Setting | Default | Notes |
|---------|---------|-------|
| `OLLAMA_MODEL` | `qwen3:14b` | Must exist locally (`ollama list`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `HOTKEY` | `<cmd>+<ctrl>+l` | pynput format: `<cmd>`, `<ctrl>`, `<shift>`, `<alt>` — **not** `<fn>` or `<option>` |
| `OLLAMA_NUM_PREDICT` | 2048 | Max output tokens (includes thinking) |
| `OLLAMA_NUM_CTX` | 4096 | Context window size |
| `OLLAMA_KEEP_ALIVE` | `30m` | Model stays loaded in memory |
| `OLLAMA_TIMEOUT` | 120 | HTTP request timeout (seconds) |
| `MAX_TEXT_LENGTH` | 12000 | Input truncation limit |

**pynput key names on macOS:** Use `<alt>` not `<option>`; use `<cmd>` not `<command>`. `<fn>` and `<option>` are rejected by `HotKey.parse`.

## Agents

Defined as `Agent` dataclasses in `config.py`. Each has an `id`, `name`, `icon` (emoji), and `system_prompt`. The default is index 1 (Code Expert). Switching agents only affects new messages; existing conversation content is unchanged.

| Agent | ID | Behavior |
|-------|-----|----------|
| 💻 Code Expert | `code_expert` | Code review, debugging, writing; structured output format |
| 🌐 Translator | `translator` | CN↔EN translation; direct output, no thinking |
| 🤖 General | `general_assistant` | Catch-all; concise Chinese responses |

## Memory System Notes

The project has associated memory files at `~/.qwen/projects/-Users-milin-2026-AI----/memory/`:
- `project_v1_scope.md` — v1 constraints and scope boundaries
- `reference_macos_accessibility_api.md` — Why AXSelectedText was dropped in favor of ⌘C simulation
- `user_design_approach.md` — User's design philosophy (constraints-first, macOS-native, minimal viable)
- `feedback_plan_before_code.md` — User prefers structured plan before implementation
- `reference_pynput_key_names_macos.md` — pynput key name quirks on macOS
- `reference_pynput_pyqt5_macos.md` — Thread safety: pynput callbacks → Qt signals

When working on this project, check these memories for context on past decisions and user preferences.

## Edge Cases & Gotchas

- **Empty selection / no change**: If clipboard content unchanged after ⌘C, `read_selection()` returns `None` and no window is opened
- **Ollama not running**: Warning logged on startup; chat will fail with connection error displayed in the chat bubble
- **Model not pulled**: Will appear in model list (fallback to config default) but API calls will fail
- **Concurrent sends**: `_on_user_message` guards against multiple simultaneous workers (`if self._worker and self._worker.isRunning(): return`)
- **Window auto-hide**: Dialog hides on focus loss (clicking outside) — by design for non-intrusive interaction
- **Cross-Space**: Float button uses `NSWindowCollectionBehaviorCanJoinAllSpaces` (value 1) via `objc_msgSend`
- **Screen tracking**: 500ms timer checks if cursor moved to a different screen and repositions the float button
- **Accessibility permissions (macOS)**: `pynput` Controller uses `CGEventPost` which requires the running process (Terminal.app / iTerm.app) to be granted Accessibility permission in System Settings → Privacy & Security → Accessibility. Without it, `read_selection()` silently fails (Cmd+C not posted). The code includes an `osascript` fallback, but that also requires the same permission for System Events.
- **Permission check at startup**: pynput logs `This process is not trusted!` as a warning when accessibility is missing — the app still runs but text capture won't work.

## Development Roadmap

See `思考与设计.md` for the full prioritized roadmap (P0–P3). Summary:

| Tier | Status | Focus |
|------|--------|-------|
| P0 | 1/3 done | Bug fixes: model combo ✅, input auto-focus, copy button |
| P1 | 0/4 done | Core UX: history browser, dark mode, error states, keyboard shortcuts |
| P2 | 0/4 done | Features: settings UI, custom agents, export, session restore |
| P3 | 0/4 done | Future: menu bar icon, image input, tests, search |
