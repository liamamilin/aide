"""Opt-in web search in the core: per-conversation switch, key handling, end-to-end tool use, cancel."""
import json
import queue
import subprocess
import sys
import threading
import time

import pytest

from ai_desktop import config
from ai_desktop.core.conversations import ConversationService
from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.runs import RunService
from ai_desktop.core.search import SearchService
from ai_desktop.llm.thinking import ThinkMode, ThinkSetting
from ai_desktop.services.http_search import HttpSearchExecutor
from ai_desktop.services.web_search import ENDPOINTS, SearchSettings
from tests.fake_ollama import FakeOllama
from tests.test_core_tools import _final, _next, _tool_turn


class Keys:
    """In-memory stand-in for the Keychain."""

    def __init__(self, key=""):
        self.key = key
        self.saved = []

    def get(self, provider, **_kw):
        return self.key

    def set(self, provider, value):
        self.key = value
        self.saved.append((provider, value))

    def delete(self, provider):
        self.key = ""


@pytest.fixture
def env(tmp_db, monkeypatch):
    llm, web = FakeOllama(), FakeOllama()
    llm.static_responses["/api/version"] = b'{"version":"0.34.3"}'
    llm.static_responses["/api/tags"] = json.dumps({"models": [{"name": "m:1", "digest": "abc"}]}).encode()
    llm.static_responses["/api/show"] = json.dumps({
        "capabilities": ["completion", "tools"], "thinking": {"values": [False, True], "default": True}}).encode()
    for attr, value in (("OLLAMA_BASE_URL", llm.url), ("OLLAMA_MODEL", "m:1"), ("OLLAMA_NUM_CTX", 32768),
                        ("OLLAMA_NUM_PREDICT", 1024), ("OLLAMA_THINK", ThinkSetting(ThinkMode.OFF)),
                        ("DESKTOP_TOOLS_ENABLED", True), ("CHAT_TOOLS_ENABLED", True)):
        monkeypatch.setattr(config, attr, value)
    for provider in ENDPOINTS:
        monkeypatch.setitem(ENDPOINTS, provider, web.url + "/search")
    events: queue.Queue = queue.Queue()
    runs = RunService(lambda name, data: events.put((name, data)))
    runs.desktop_runner = lambda argv: ""
    keys = Keys("k-test-only")
    search = SearchService(keys)
    chats = ConversationService(runs, search)
    yield chats, search, keys, llm, web, events
    llm.close()
    web.close()


def test_switch_needs_a_saved_key_and_persists(env):
    chats, search, keys, *_ = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    assert chats.get({"id": convo})["search"] is False
    keys.key = ""
    with pytest.raises(ProtocolError):
        search.set_conversation({"conversation_id": convo, "enabled": True})
    keys.key = "k"
    search.set_conversation({"conversation_id": convo, "enabled": True})
    assert chats.get({"id": convo})["search"] is True
    search.set_conversation({"conversation_id": convo, "enabled": False})
    assert chats.get({"id": convo})["search"] is False
    with pytest.raises(ProtocolError):
        search.set_conversation({"conversation_id": 9999, "enabled": True})


def test_key_is_saved_but_never_returned(env):
    _chats, search, keys, *_ = env
    keys.key = ""
    out = search.save_key({"provider": "exa", "key": "  secret-123  "})
    assert keys.saved == [("exa", "secret-123")] and out["has_key"] is True and out["provider"] == "exa"
    assert "secret-123" not in json.dumps(search.status({}))
    with pytest.raises(ProtocolError):
        search.save_key({"provider": "bing", "key": "x"})
    assert search.delete_key({})["has_key"] is False


def test_search_off_means_no_search_tool(env):
    chats, _search, _keys, llm, *_ = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    llm.enqueue(*_final("ok"))
    assert "web_search" not in chats.send({"conversation_id": convo, "text": "今天新闻"})["tools"]


def test_search_on_runs_the_query_and_feeds_sources_to_the_model(env):
    chats, search, _keys, llm, web, events = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    search.set_conversation({"conversation_id": convo, "enabled": True})
    web.enqueue({"results": [{"title": "Py", "url": "https://docs.python.org/", "excerpts": ["docs"],
                              "highlights": ["docs"]}]})
    llm.enqueue(_tool_turn("web_search", {"query": "python"}))
    llm.enqueue(*_final("见 [S1]"))
    started = chats.send({"conversation_id": convo, "text": "python 官网"})
    assert "web_search" in started["tools"]
    assert _next(events, "run.finished")["text"] == "见 [S1]"
    tool_message = llm.requests[1]["payload"]["messages"][-1]
    assert tool_message["role"] == "tool" and "docs.python.org" in tool_message["content"]
    assert "k-test-only" not in tool_message["content"]


def test_search_on_without_key_runs_without_the_tool_and_says_why(env):
    chats, search, keys, llm, *_ = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    search.set_conversation({"conversation_id": convo, "enabled": True})
    keys.key = ""                                          # removed after switching on
    llm.enqueue(*_final("ok"))
    started = chats.send({"conversation_id": convo, "text": "新闻"})
    assert "web_search" not in started["tools"] and "密钥" in started["tools_note"]


def test_stop_ends_a_slow_search_promptly(env):
    _chats, _search, keys, _llm, web, _events = env
    web.enqueue(hold_open=True, chunks=[b""])
    executor = HttpSearchExecutor(SearchSettings(), credentials=keys)
    cancelled = threading.Event()

    class Ctx:
        def __init__(self):
            self.cancelled, self.active_deadline, self.on_state = cancelled, None, None

    threading.Timer(0.2, cancelled.set).start()
    began = time.monotonic()
    out = executor({"query": "python"}, Ctx())
    assert time.monotonic() - began < 3 and json.loads(out.text)["error_type"] == "cancelled"


def test_core_modules_stay_qt_free():
    code = ("import sys; import ai_desktop.core.search, ai_desktop.core.conversations, "
            "ai_desktop.services.http_search; print(any(m.startswith('PyQt5') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


