"""Spotlight-backed discovery of applications and files; returns metadata only."""
import os
from dataclasses import dataclass
from pathlib import Path

from ai_desktop.desktop.runner import MDFIND, Runner, run_command

APP_TYPE = "com.apple.application-bundle"
APP_ROOTS = ("/Applications", "/System/Applications", "/System/Applications/Utilities")
MAX_QUERY_LENGTH = 200


@dataclass(frozen=True)
class Candidate:
    kind: str  # "app", "file" or "folder"
    name: str
    path: str


def default_file_roots() -> tuple[str, ...]:
    home = Path.home()
    return tuple(str(home / name) for name in ("Documents", "Desktop", "Downloads"))


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
    text = _quote(_clean(query))
    predicate = (f'kMDItemContentType == "{APP_TYPE}" && (kMDItemDisplayName == "*{text}*"wc '
                 f'|| kMDItemAlternateNames == "*{text}*"wc)')
    paths = runner([MDFIND, *_onlyin(roots or app_roots()), predicate]).splitlines()
    return [Candidate("app", _display_name(path), path) for path in paths if path.endswith(".app")][:limit]


def find_files(query: str, *, runner: Runner = run_command, roots=None, limit: int = 8) -> list[Candidate]:
    """Match file and folder names under the search roots, excluding app bundles."""
    text = _quote(_clean(query))
    predicate = f'kMDItemDisplayName == "*{text}*"wcd && kMDItemContentType != "{APP_TYPE}"'
    paths = runner([MDFIND, *_onlyin(roots or default_file_roots()), predicate]).splitlines()
    found = []
    for path in paths:
        if not path.startswith("/") or path.endswith(".app"):
            continue
        found.append(Candidate("folder" if os.path.isdir(path) else "file", os.path.basename(path), path))
        if len(found) >= limit:
            break
    return found
