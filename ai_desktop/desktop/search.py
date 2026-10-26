"""Spotlight-backed discovery of applications and files; returns metadata only."""
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ai_desktop import config
from ai_desktop.desktop.runner import MDFIND, Runner, run_command

APP_TYPE = "com.apple.application-bundle"
APP_ROOTS = ("/Applications", "/System/Applications", "/System/Applications/Utilities")
MAX_QUERY_LENGTH = 200
APP_CACHE_SECONDS = 60.0
_app_cache: dict[tuple, tuple[float, list]] = {}
_app_cache_lock = threading.Lock()


@dataclass(frozen=True)
class Candidate:
    kind: str  # "app", "file" or "folder"
    name: str
    path: str


# Bulk or system data that would only add noise (and private app data) to a name search of the home folder.
_SKIPPED_PARTS = frozenset({"Library", "node_modules", ".git", ".Trash", "venv", ".venv", "__pycache__",
                            "site-packages"})


def default_file_roots() -> tuple[str, ...]:
    """The configured search folders, or the whole home folder."""
    configured = tuple(root for root in config.FILE_SEARCH_ROOTS if isinstance(root, str) and root.startswith("/"))
    return configured or (str(Path.home()),)


def _is_skipped(path: str) -> bool:
    parts = Path(path).parts
    return any(part in _SKIPPED_PARTS or (part.startswith(".") and part not in (".", "..")) for part in parts[1:])


def app_roots() -> tuple[str, ...]:
    return APP_ROOTS + (str(Path.home() / "Applications"),)


def _quote(text: str) -> str:
    """Escape for a Spotlight string literal; wildcards the user typed are literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("*", "\\*")


def _clean(query: str) -> str:
    query = " ".join(str(query).split())
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise ValueError("Invalid search query")
    return query


def _onlyin(roots) -> list[str]:
    args: list[str] = []
    for root in roots:
        args += ["-onlyin", root]
    return args


def _display_name(path: str) -> str:
    name = os.path.basename(path.rstrip("/"))
    return name[:-4] if name.endswith(".app") else name


def find_apps(query: str, *, runner: Runner = run_command, roots=None, limit: int = 8) -> list[Candidate]:
    """Match localized and alternate (English) application names."""
    cleaned = _clean(query)
    # Installed apps change rarely; repeating a query while typing should not hit Spotlight again.
    key = (cleaned.casefold(), tuple(roots or ()), limit)
    if runner is run_command:
        with _app_cache_lock:
            hit = _app_cache.get(key)
        if hit and time.monotonic() - hit[0] < APP_CACHE_SECONDS:
            return list(hit[1])
    text = _quote(cleaned)
    predicate = (f'kMDItemContentType == "{APP_TYPE}" && (kMDItemDisplayName == "*{text}*"wc '
                 f'|| kMDItemAlternateNames == "*{text}*"wc)')
    paths = runner([MDFIND, *_onlyin(roots or app_roots()), predicate]).splitlines()
    found = [Candidate("app", _display_name(path), path) for path in paths if path.endswith(".app")][:limit]
    if runner is run_command:
        with _app_cache_lock:
            _app_cache[key] = (time.monotonic(), list(found))
    return found


def find_files(query: str, *, runner: Runner = run_command, roots=None, limit: int = 8) -> list[Candidate]:
    """Match file and folder names under the search roots, excluding app bundles."""
    # `mdfind -name` matches Chinese names mid-string; a leading-wildcard attribute predicate does not.
    paths = runner([MDFIND, *_onlyin(roots or default_file_roots()), "-name", _clean(query)]).splitlines()
    found = []
    for path in paths:
        if not path.startswith("/") or path.endswith(".app") or _is_skipped(path):
            continue
        found.append(Candidate("folder" if os.path.isdir(path) else "file", os.path.basename(path), path))
        if len(found) >= limit:
            break
    return found
