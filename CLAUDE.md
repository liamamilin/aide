# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

macOS-only PyQt5 desktop assistant (`aide` entry point, package `ai_desktop/`). Select text → global hotkey (`⌘⌃L`) or screenshot (`⌘⌃S`) → chat with a **local Ollama** model. Constraint carried from v1: local LLM only, no data uploaded. Python ≥ 3.10; UI is PyQt5 + PyQt-Fluent-Widgets. Docs and many comments are in Chinese. `QWEN.md` is an older, partly stale project overview — trust the code over it.

## Commands

```bash
pip install -r requirements-dev.txt && pip install -e .   # dev setup (pytest, pytest-qt, ruff, pyinstaller)
aide                                                       # run the app (needs `ollama serve`)

ruff check ai_desktop/ tests/ scripts/release_check.py     # lint (CI scope; line-length 120)
QT_QPA_PLATFORM=offscreen pytest tests/ -q                 # full suite (as in CI)
QT_QPA_PLATFORM=offscreen pytest tests/test_run_loop.py::test_name -q   # single test

python scripts/release_check.py [--version X.Y.Z --require-new-tag]    # read-only version/release validation
./scripts/build.sh [--test] [--smoke] [--dmg]              # PyInstaller .app build (+ruff/pytest, smoke, DMG)
python scripts/restore_database.py <backup.sqlite3>        # restore a DB backup (stop the app first)
```

CI runs on macOS for Python 3.10/3.11/3.12: ruff → `release_check.py` → pytest.

## Environment / data

- `ai_desktop/version.py` is the single version source (packaging, app, Info.plist, DMG names).
- `AIDE_DATA_DIR` redirects the SQLite DB and attachments (default `~/Library/Application Support/ai-desktop-assistant/`; a dev-mode fallback exists in `utils/storage.py`). `AIDE_LOG_DIR`, `AIDE_SIGN_IDENTITY`, `AIDE_SMOKE_TEST` are also read.
- `tests/conftest.py` **forces** `AIDE_DATA_DIR` to a session temp dir before `storage` is imported (DB path resolves once at import). Never import `ai_desktop.utils.storage` before that in test helpers, and never let tests touch real user data. It also keeps Qt windows alive through teardown via `qtbot`; follow the existing Qt lifecycle tests (`test_qt_*_lifecycle.py`) when adding widget/worker tests.
- `tests/fake_ollama.py` is a loopback fake Ollama server for network tests (avoid DNS lookups — use IP literals).
- `.env` is gitignored; don't commit it.

## Architecture

**Orchestration.** `ai_desktop/main.py` (~2900 lines) holds `ChatController`, which wires everything: hotkey/clipboard capture (`capture/`), floating button / menu bar icon / chat dialog / pet (`ui/`), settings, agents, and launching runs. Slots are wrapped with `_safe_slot` so exceptions don't kill the Qt event loop.

**Unified run engine (`llm/`).** All model interaction — plain chat and tool tasks — goes through one pipeline:
- `run_worker.py` `RunWorker(QThread)`: does attachment I/O and all model steps off the UI thread; emits thinking/content/done signals.
- `run_loop.py` `RunLoop`: single sequential engine (model turn → tool calls → next turn) independent of the transport. Executors are injected trusted code; tests use synthetic executors, never a real shell or paid API. Context-length recovery is allowed only on an explicit numeric server rejection.
- `qt_stream.py` + `ollama_protocol.py`: incremental NDJSON transport over `QNetworkAccessManager` (create and use in one Qt event-loop thread), with `TurnAssembler` building turns. `chat_client.py` is the `requests`-based non-streaming client. Events/IDs (`RequestContext` with request/run/step/conversation ids) live in `events.py` and `run_types.py`; stale or cross-conversation events are dropped by ID.
- `harness_probe.py` / `service_checks.py` / `task_checks.py`: per-model capability checks gating whether tools may run ("tool runs follow the selected model after a fresh capability check").

**Tool registry (`llm/tool_registry.py`).** Each tool's schema, argument validation, group, max risk and budget live in a `ToolDefinition`; `run_types`/`run_loop` look tools up by name instead of branching. Registering a tool does not grant it: chat access is the allow-list `config.CHAT_TOOL_NAMES`, and runs still need admission. Desktop discovery (`desktop/`: Spotlight `mdfind` search, `open`, ranked `resolver`) is shared by the chat-input candidate list (`ui/candidate_panel.py`, never calls a model) and the `find_apps`/`find_files`/`open_path` tools; `DESTRUCTIVE` native tools (e.g. `delete_note`) fail closed unless the toolbox has a `ConfirmationBroker`, and ask via `CommandConfirmation.for_action` with the exact preview; system utilities go through an injectable runner, and tests must never run the real `mdfind`/`open` (conftest turns `LOCAL_CANDIDATES_ENABLED` and `DESKTOP_TOOLS_ENABLED` off). Design/plan: `docs/Aide个人电脑入口*.md`.

**Tools (`services/`).** Bash, Parallel and Exa web search are opt-in per conversation/agent; choices are inherited across conversations (`tool_preferences.py`). Safety layers: `bash_policy.py` (tiny literal-command grammar; anything outside needs exact-command confirmation — shlex splitting is never authorization), `command_confirmation.py`, `bash_executor.py`, `execution_context.py` (snapshots), `task_admission.py`, `search_executor.py`/`web_search.py`, `credential_broker.py`/`credential_reader.py` (macOS Keychain via a small native broker, see `native/` and `scripts/build_credential_broker.py`), and `audit_store.py`/`run_audit.py` for run history.

**Qt reply lifecycle.** `services/qt_reply.py` works around PyQt5 stale-address destruction notifications that can invalidate a new `QNetworkReply` wrapper: each caller owns a fresh manager with exactly one request and resolves the reply via the manager's child list, never a saved pointer. Don't bypass `call_reply`/`dispose_reply` when touching `QNetworkReply`. Related repro scripts: `scripts/reproduce_qt_reply_*.py`; background in `docs/Qt网络回复生命周期*.md`.

**Persistence.** `utils/storage.py`: SQLite (WAL, thread-local connections) with versioned schema migrations; an existing unversioned DB is backed up to `backups/` before upgrade and migrations are transactional. `settings_manager.py`, `agent_manager.py` (5 built-in + custom agents), `services/model_profiles.py` (per-agent model/thinking/temperature overrides that inherit global settings).

**Pets / UI.** Floating pet rendering uses spritesheets and layer manifests (`ui/pet_*.py`, `ai_desktop/pets/`, `pet_layers/`); `scripts/audit_pet_*.py` validate pet assets. New package data must be added to `package_data` in `setup.py`.

**Diagnostics.** `ai_desktop/diagnostics/` and `scripts/harness_*.py` are acceptance/diagnostic runners (real-app flows, search, tool inheritance, desktop recovery) — separate from the pytest suite.

## Docs

`docs/` holds dated design/acceptance notes (Chinese), e.g. the H0–H6 harness series describing the unified runtime, command execution boundary, search, and audit. Consult the relevant one before changing those subsystems. `CHANGELOG.md` tracks releases.
