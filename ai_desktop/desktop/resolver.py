"""Turn a typed phrase into ranked local candidates without involving a model."""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from ai_desktop.desktop.runner import Runner, run_command
from ai_desktop.desktop.search import Candidate, find_apps, find_files

logger = logging.getLogger(__name__)

MAX_SHORT_QUERY = 24
_SENTENCE_MARKS = re.compile(r"[，。！？；：,.!?;:\n]")
# Words that mark an instruction or question rather than a name.
_REQUEST_WORDS = re.compile(r"[把帮请让给吗呢么]|怎么|如何|什么|为什么|整理|总结|翻译|解释")
MAX_SHORT_WORDS = 4
_KIND_ORDER = {"app": 0, "folder": 1, "file": 2}


def is_short_query(text: str) -> bool:
    """A name-like phrase worth a local lookup; sentences go to the model instead."""
    text = (text or "").strip()
    return (bool(text) and len(text) <= MAX_SHORT_QUERY and len(text.split()) <= MAX_SHORT_WORDS
            and not _SENTENCE_MARKS.search(text) and not _REQUEST_WORDS.search(text))


def _rank(query: str, candidate: Candidate):
    name, query = candidate.name.casefold(), query.casefold()
    match = 0 if name == query else 1 if name.startswith(query) else 2
    return match, _KIND_ORDER.get(candidate.kind, 3), len(name), name


def resolve(query: str, *, runner: Runner = run_command, limit: int = 8) -> list[Candidate]:
    """Applications and files matching the query, best first. Lookup failures yield fewer results."""
    query = (query or "").strip()
    if not is_short_query(query):
        return []
    found: list[Candidate] = []
    # The two Spotlight queries are independent; run them together so a slow machine pays once.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finder, query, runner=runner, limit=limit) for finder in (find_apps, find_files)]
        for future in futures:
            try:
                found.extend(future.result())
            except (OSError, ValueError) as exc:
                logger.warning("local lookup failed: %s", exc)
    seen, unique = set(), []
    for candidate in sorted(found, key=lambda item: _rank(query, item)):
        if candidate.path not in seen:
            seen.add(candidate.path)
            unique.append(candidate)
    return unique[:limit]
