"""Turn a typed phrase into ranked local candidates without involving a model."""
import logging
import re

from ai_desktop.desktop.runner import Runner, run_command
from ai_desktop.desktop.search import Candidate, find_apps, find_files

logger = logging.getLogger(__name__)

MAX_SHORT_QUERY = 24
_SENTENCE_MARKS = re.compile(r"[，。！？；：,.!?;:\n]")
# Words that mark an instruction or question rather than a name.
_REQUEST_WORDS = re.compile(r"[把帮请让给吗呢么]|怎么|如何|什么|为什么|整理|总结|翻译|解释")
MAX_SHORT_WORDS = 4
_KIND_ORDER = {"app": 0, "folder": 1, "file": 2}
MAX_ACTION_REQUEST = 60
_OPEN_VERBS = re.compile(r"^(请|帮我|麻烦)?\s*(打开|启动)|^(please\s+)?(open|launch)\b", re.IGNORECASE)
_FIND_VERBS = re.compile(r"^(请|帮我|麻烦)?\s*(找一下|找到|找出|找|查找|定位)|^(please\s+)?(find|locate|reveal)\b",
                         re.IGNORECASE)
_LOCAL_OBJECTS = re.compile(r"文件|文档|应用|软件|目录|项目|表格|笔记|照片|图片|pdf|docx|xlsx|pptx|\bapp\b",
                            re.IGNORECASE)
_CAPTURE_VERBS = re.compile(r"^(请|帮我|麻烦)?\s*(记一下|记下|记录|新建|创建|提醒我|建一条)", re.IGNORECASE)
_CAPTURE_OBJECTS = re.compile(r"笔记|备忘|提醒|待办|想法|\bnote\b|\breminder\b|remind me", re.IGNORECASE)
_WINDOW_WORDS = re.compile(r"窗口|\bwindow", re.IGNORECASE)
_LAYOUT_WORDS = re.compile(r"并排|摆放|排列|最大化|左半|右半|居中|side by side|maximi[sz]e", re.IGNORECASE)
_WEB_WORDS = re.compile(r"网上|网页|网络|联网|在线|搜索引擎|百度|google|bing|online|web", re.IGNORECASE)


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
    for finder in (find_apps, find_files):
        try:
            found.extend(finder(query, runner=runner, limit=limit))
        except (OSError, ValueError):
            logger.warning("local lookup failed", exc_info=True)
    seen, unique = set(), []
    for candidate in sorted(found, key=lambda item: _rank(query, item)):
        if candidate.path not in seen:
            seen.add(candidate.path)
            unique.append(candidate)
    return unique[:limit]


def wants_desktop_action(text: str) -> bool:
    """Whether a message asks to open or locate something on this Mac (not a web or general question)."""
    text = (text or "").strip()
    if not text or len(text) > MAX_ACTION_REQUEST or "\n" in text or _WEB_WORDS.search(text):
        return False
    if _OPEN_VERBS.search(text):
        return True
    if _CAPTURE_VERBS.search(text) and _CAPTURE_OBJECTS.search(text):
        return True
    if _WINDOW_WORDS.search(text) and _LAYOUT_WORDS.search(text):
        return True
    return bool(_FIND_VERBS.search(text) and _LOCAL_OBJECTS.search(text))
