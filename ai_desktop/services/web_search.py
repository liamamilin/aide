"""Provider adapters for the single web_search tool.

Only a query/objective crosses the tool boundary. Provider, mode, bounds and
credentials come from an immutable application snapshot, not model arguments.
No implicit retries, provider fallback, extraction, or shared search sessions.
"""
import json
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

ENDPOINTS = {"parallel": "https://api.parallel.ai/v1/search", "exa": "https://api.exa.ai/search"}
PARALLEL_MODES = frozenset({"turbo", "fast", "basic", "advanced"})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_RESULTS = 99


class SearchError(Exception):
    pass


@dataclass(frozen=True)
class SearchSettings:
    provider: str = "parallel"
    max_results: int = 5
    timeout: int = 30
    parallel_mode: str = "basic"

    @classmethod
    def from_config(cls):
        from ai_desktop import config
        return cls(config.SEARCH_PROVIDER, config.SEARCH_MAX_RESULTS,
                   config.SEARCH_TIMEOUT, config.SEARCH_PARALLEL_MODE)

    def record(self):
        return asdict(self)

    def __post_init__(self):
        if self.provider not in ENDPOINTS or self.parallel_mode not in PARALLEL_MODES:
            raise SearchError("搜索服务或模式无效。")
        if type(self.max_results) is not int or not 1 <= self.max_results <= MAX_SEARCH_RESULTS:
            raise SearchError(f"搜索来源数量必须为 1–{MAX_SEARCH_RESULTS}。")
        if type(self.timeout) is not int or not 5 <= self.timeout <= 120:
            raise SearchError("搜索超时必须为 5–120 秒。")


@dataclass(frozen=True)
class SearchRequest:
    settings: SearchSettings
    payload: bytes = field(repr=False)
    api_key: str = field(repr=False)

    @property
    def url(self):
        return ENDPOINTS[self.settings.provider]


@dataclass(frozen=True)
class SearchSource:
    source_id: str
    title: str
    url: str
    excerpt: str
    published_at: str = ""


@dataclass(frozen=True)
class SearchResult:
    sources: tuple[SearchSource, ...] = ()
    error: str = ""
    cancelled: bool = False
    error_type: str = ""

    @property
    def ok(self):
        return not self.error and not self.cancelled


def build_request(settings: SearchSettings, api_key: str, query: str, objective: str = "") -> SearchRequest:
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise SearchError("搜索词不能为空或超过 1000 个字符。")
    if not isinstance(objective, str) or len(objective) > 4096:
        raise SearchError("搜索目的不能超过 4096 个字符。")
    if not isinstance(api_key, str) or not api_key or len(api_key) > 4096 or any(c.isspace() for c in api_key):
        raise SearchError("请在设置中填写有效的搜索 API 密钥。")
    if settings.provider == "parallel":
        payload = {"search_queries": [query.strip()], "mode": settings.parallel_mode, "max_chars_total": 6000}
    else:
        payload = {"query": query.strip(), "type": "auto", "numResults": settings.max_results,
                   "contents": {"highlights": True}}
    if objective.strip():
        payload["objective"] = objective.strip()
    return SearchRequest(settings, json.dumps(payload, ensure_ascii=False).encode("utf-8"), api_key)


def http_error(status: int) -> str:
    if status in (401, 403):
        return "搜索服务拒绝访问，请检查 API 密钥和权限。"
    if status == 402:
        return "搜索账户额度不足，请检查服务账户。"
    if status == 429:
        return "搜索服务请求过多，请稍后重试。"
    if 300 <= status < 400:
        return "搜索服务返回重定向，已停止请求。"
    return f"搜索服务返回 HTTP {status}，请稍后重试。"


def safe_source_url(url):
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme in ("https", "http") and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url))
    except ValueError:
        return False


def parse_response(settings: SearchSettings, raw: bytes) -> SearchResult:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SearchError("搜索响应过大，已停止接收。")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise SearchError("搜索服务响应格式错误。") from None
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise SearchError("搜索服务响应缺少来源列表。")
    sources = []
    seen = set()
    for entry in value["results"]:
        if not isinstance(entry, dict):
            raise SearchError("搜索来源格式错误。")
        url = entry.get("url", "")
        if not safe_source_url(url) or url in seen:
            continue
        parts = urlsplit(url)
        seen.add(url)
        title = entry.get("title") or parts.hostname
        date = entry.get("publish_date" if settings.provider == "parallel" else "publishedDate") or ""
        excerpts = entry.get("excerpts" if settings.provider == "parallel" else "highlights", [])
        if not isinstance(title, str) or not isinstance(date, str) or not isinstance(excerpts, list):
            raise SearchError("搜索来源格式错误。")
        if any(not isinstance(item, str) for item in excerpts):
            raise SearchError("搜索摘要格式错误。")
        sources.append(SearchSource(f"S{len(sources) + 1}", title[:300], url,
                                    "\n".join(excerpts)[:1200], date[:40]))
        if len(sources) >= settings.max_results:
            break
    return SearchResult(tuple(sources))
