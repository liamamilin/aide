# Changelog

This project follows source-controlled release versions. A version listed as
"Candidate" has been built for verification but has not been published as a
GitHub Release.

## Unreleased — M3 development

- Added reusable task model profiles for model, thinking mode, temperature, and
  output limit, with per-field inheritance and Agent assignment.
- Added deterministic request-option resolution and immutable submission
  snapshots, including visible fallback warnings for missing models or deleted
  profiles.
- Added profile management and effective-configuration summaries to the Agent
  and chat interfaces.
- Preserved all enumerable macOS clipboard formats during selection capture and
  skipped restoration when the user copies newer content.
- Added four validated quick actions with persistent Agent/profile settings and
  a keyboard-driven action bar after text selection.

## 1.5.0 — Candidate

- Added managed attachment validation, reference tracking, inference copies, and
  safe garbage collection while preserving legacy image history.
- Added asynchronous Ollama image-capability checks with supported,
  unsupported, and unknown states before image submission.
- Added stable cursor pagination and validated title renaming for all history,
  including custom Agent identity display.
- Persisted chat-window geometry and floating-button placement, with recovery
  after monitor removal, resolution changes, or invalid saved state.
- Added explicit SQLite schema versions, consistent online backups before
  upgrades, transactional rollback, future-version rejection, and a guarded
  restore utility that retains the current database as a safety backup.
- Added a reproducible M2 benchmark for 1,000-conversation history, long
  conversations, attachment processing, window construction, and request
  preparation.

No `v1.5.0` tag or GitHub Release has been created yet.

## 1.4.1 — Candidate

- Added explicit request/result contracts and isolated late stream events by
  request and conversation.
- Replaced blocking HTTP paths with cancellable Qt networking and asynchronous
  service, model, and update checks.
- Coordinated shutdown across generation, capture, hotkey, and background tasks.
- Moved text selection capture off the GUI thread and classified screenshot
  cancellation, permission, timeout, and command failures.
- Refreshed all existing windows, menus, and Markdown content when the macOS
  palette changes; kept child dialogs in their parent interaction flow.
- Unified runtime, package, bundle, About dialog, and DMG versions in
  `ai_desktop/version.py`.
- Unified local and CI builds through `scripts/aide.spec`, added read-only release
  preflight, and isolated packaged-app smoke data from the user's real data.

No `v1.4.1` tag or GitHub Release has been created yet.
