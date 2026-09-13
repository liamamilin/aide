# Changelog

This project follows source-controlled release versions. A version listed as
"Candidate" has been built for verification but has not been published as a
GitHub Release.

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
