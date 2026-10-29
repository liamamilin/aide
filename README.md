<h1 align="center">Aide</h1>
<p align="center"><strong>Your Mac's entry point to a local AI.</strong><br>
Press <code>⌘⌃L</code>, say what you want — or select text first — and it gets done.<br>
Runs on your own Ollama models. Nothing is uploaded.</p>

<p align="center"><a href="README.zh.md">中文</a> | <strong>English</strong></p>

<p align="center">
<img alt="macOS 14+" src="https://img.shields.io/badge/macOS-14%2B-lightgrey.svg">
<img alt="Apple silicon" src="https://img.shields.io/badge/Apple%20silicon-arm64-lightgrey.svg">
<img alt="Ollama" src="https://img.shields.io/badge/LLM-Ollama-orange.svg">
<img alt="SwiftUI" src="https://img.shields.io/badge/UI-SwiftUI%20%2F%20AppKit-blue.svg">
</p>

<p align="center">
<picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/screenshots/quick-panel-actions-dark.png"><img alt="Quick Panel with a text selection and quick actions" src="docs/assets/screenshots/quick-panel-actions.png" width="440"></picture>&nbsp;&nbsp;<picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/screenshots/quick-panel-results-dark.png"><img alt="Quick Panel showing files it found" src="docs/assets/screenshots/quick-panel-results.png" width="440"></picture>
<br><sub>Select text, pick an action &nbsp;·&nbsp; Ask for something, open what it finds</sub>
</p>

---

## Why Aide

Most AI tools make you go to them: open a tab, paste, wait, copy back. Aide goes the other way. It lives in your menu bar, opens over whatever you are doing, and hands the result back to the app you came from.

- **Private by design.** Only a local [Ollama](https://ollama.com) server is used. No account, no cloud, no telemetry, and your conversations stay in a database on your Mac.
- **One shortcut, two jobs.** Type a request, or select text and pick an action. Either way it is one keystroke away.
- **It can act, but never behind your back.** Aide can find apps and files, open them, and read or change Notes and Reminders. Anything that changes data shows the exact action first and waits for your approval.
- **Native and quiet.** A SwiftUI/AppKit app that follows light/dark mode and your system accent. No dock icon to manage, no window until you ask for one.

## What you can do

| You | Aide |
|---|---|
| Press `⌘⌃L` and type *"open Notes"*, *"find the PDF I changed most recently"*, *"how many notes do I have"* | Runs it with local desktop tools. Apps and files it finds appear as items you can open, or reveal in Finder. |
| Select text in any app, press `⌘⌃L` | Choose **Translate**, **Explain**, **Summarize**, **Rewrite**, or one of your own actions. Or type your own instruction. |
| Press `⌘↩` on an answer | Continue the conversation in the Workspace. |
| Press `↩` on an answer | Copy it and return to the app you came from. |
| Press `⌘⌃S` and drag a region | Starts a Workspace chat with the screenshot attached (needs a vision-capable model). |
| Ask it to delete a note, append to a note, or change a reminder | Shows exactly what will happen. Nothing runs until you allow it. |

### Quick Panel

The fast path. A small panel opens over your current app.

- Type freely, including Chinese and other input methods. Digits and arrows are just text while you type.
- Pick a quick action with the mouse, `⌘1`–`⌘9`, or `⌘↑` / `⌘↓` then `↩`.
- `Esc` stops a running answer immediately. The input comes back so you can ask something else, and the old reply never leaks into the new one.
- Every typed request is independent. There is no hidden context carried between questions.
- If you stop a task after it already changed something, Aide says so: finished actions are not undone.

### Workspace

The full window, opened from the menu bar.

- Conversation history with search, restored exactly as you left it, including found files and apps.
- Streaming Markdown: tables, lists, and code blocks with a copy button.
- Regenerate an answer, edit your last question, switch agent or model per conversation.
- Long answers keep up with the stream without freezing the window.

### Agents and custom actions

Five built-in agents, plus your own with their own system prompt, model, thinking mode and sampling. Custom quick actions turn a prompt you use often into one key on the Quick Panel.

## Install

**Requirements:** macOS 14 or later, Apple silicon, and [Ollama](https://ollama.com).

1. Download `Aide-<version>-arm64.dmg` from [Releases](https://github.com/liamamilin/aide/releases) and drag **Aide** into Applications.
2. Start Ollama and pull a model:
   ```bash
   ollama serve
   ollama pull qwen3:14b
   ```
3. Open Aide. It appears in the menu bar. The first time, macOS will ask for permissions (see below).

> **Not notarized yet.** There is no Developer ID certificate, so Gatekeeper blocks a downloaded copy the first time. Open it once, then go to System Settings → Privacy & Security → **Open Anyway**. Or run:
> ```bash
> xattr -dr com.apple.quarantine /Applications/Aide.app
> ```

There is no auto-update yet. To update, quit Aide and replace it in Applications.

### Permissions

| Permission | Why | When |
|---|---|---|
| Accessibility | Read the text you selected in other apps | First `⌘⌃L` with a selection. Typing a request works without it. |
| Screen Recording | Capture the region you drag | First `⌘⌃S` |
| Automation (Notes, Reminders) | Read or change them when you ask | First time you ask for it |

Release DMGs are ad-hoc signed, so macOS may ask again after an update. A build signed with a stable identity keeps its permissions across updates.

## Settings

Menu bar → **设置…** (`⌘,`)

- Ollama address and thinking mode
- A recordable Quick Panel hotkey
- Custom agents and custom quick actions
- Sampling parameters under "Advanced"

## Privacy and safety

- **Local only.** Requests go to the Ollama address you configure, which defaults to your own machine.
- **Your data stays put.** History and attachments live in `~/Library/Application Support/ai-desktop-assistant/`. Delete that folder to remove everything.
- **Confirm before changing.** Deleting or editing Notes and Reminders always shows the exact action and waits for you.
- **Honest results.** Found files and apps come straight from the tool, not from the model's wording. If a file has moved, opening it fails with a clear message instead of pretending.

## Not in the native app yet

The desktop pet, web search and the Bash tool still live in the [legacy Qt app](docs/legacy-qt.md), which shares the same history.

## Build from source

```bash
pip install -r requirements-dev.txt && pip install -e .
./scripts/build_native.sh --smoke      # dist/Aide.app and dist/Aide-<version>-arm64.dmg
cd macos && swift test                 # Swift tests, against the real core and a fake Ollama
```

Needs Xcode 15+ and Python 3.10+. Run from source with `cd macos && swift run Aide`.

```bash
ruff check ai_desktop/ tests/ scripts/release_check.py
QT_QPA_PLATFORM=offscreen pytest tests/ -q
```

### How it fits together

A native SwiftUI/AppKit shell (`macos/`) talks to a Qt-free Python core (`ai_desktop/core`) over NDJSON on stdio. No network port is opened, and the core exits with the app. Every model turn and tool call goes through one run engine, with per-tool risk limits and confirmation for anything destructive.

More: [macos/README.md](macos/README.md) · [design notes in `docs/`](docs/) · [CHANGELOG](CHANGELOG.md)

## Troubleshooting

- **"Can't reach Ollama"** — run `ollama serve`, then check the address in Settings.
- **`⌘⌃L` does nothing** — another app may own the shortcut. Record a different one in Settings.
- **Selected text is not picked up** — grant Accessibility in System Settings → Privacy & Security. Typing a request does not need it.
- **Screenshot answers ignore the image** — pick a model that supports images.
- **Permissions asked again after an update** — expected with ad-hoc signed builds.
