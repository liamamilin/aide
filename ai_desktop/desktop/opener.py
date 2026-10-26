"""Open or reveal an existing path. Anything that can execute is only revealed."""
import os

from ai_desktop.desktop.runner import OPEN, Runner, run_command
from ai_desktop.desktop.search import app_roots

# Opening these would run code or install software, so they are shown in Finder instead.
REVEAL_ONLY_SUFFIXES = frozenset({
    ".command", ".sh", ".zsh", ".bash", ".csh", ".tool", ".pkg", ".mpkg", ".terminal", ".workflow",
    ".scpt", ".scptd", ".applescript", ".action", ".jar", ".dmg", ".iso", ".py", ".pl", ".rb", ".js",
    ".webloc", ".inetloc", ".fileloc", ".app.zip", ".exe", ".bat",
})


def _validated(path) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError("A canonical absolute path is required")
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise ValueError("Path does not exist")
    return path


def open_action(path: str) -> str:
    """Return "open" or "reveal" for a path."""
    lowered = path.lower().rstrip("/")
    if lowered.endswith(".app"):
        # Launching runs code, so only applications in the standard locations are launched.
        return "open" if any(path.startswith(root + "/") for root in app_roots()) else "reveal"
    if any(lowered.endswith(suffix) for suffix in REVEAL_ONLY_SUFFIXES):
        return "reveal"
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return "reveal"
    return "open"


def reveal_path(path: str, *, runner: Runner = run_command) -> str:
    runner([OPEN, "-R", _validated(path)])
    return "reveal"


def open_path(path: str, *, runner: Runner = run_command) -> str:
    """Open the path, or reveal it when opening could execute code. Returns the action taken."""
    path = _validated(path)
    if open_action(path) == "reveal":
        return reveal_path(path, runner=runner)
    runner([OPEN, path])
    return "open"
