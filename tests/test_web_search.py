# ruff: noqa: N815 - match native Security constants
import json

import pytest

from ai_desktop.services.search_credentials import CredentialError, SearchCredentials
from ai_desktop.services.web_search import (
    MAX_RESPONSE_BYTES,
    SearchError,
    SearchSettings,
    build_request,
    http_error,
    parse_response,
)


@pytest.mark.parametrize("provider", ["exa", "parallel"])
def test_request_uses_app_provider_and_bounds(provider):
    request = build_request(SearchSettings(provider, 3), "secret-example", "test query", "research goal")
    data = json.loads(request.payload)
    assert data["objective"] == "research goal"
    assert "secret-example" not in repr(request)
    assert "secret-example" not in request.payload.decode()
    if provider == "parallel":
        assert request.url == "https://api.parallel.ai/v1/search"
        assert data == {"objective": "research goal", "search_queries": ["test query"],
                        "mode": "basic", "max_chars_total": 6000}
    else:
        assert request.url == "https://api.exa.ai/search"
        assert data["numResults"] == 3
        assert data["contents"] == {"highlights": True}


@pytest.mark.parametrize("kwargs", [{"provider": "other"}, {"max_results": 100}, {"max_results": True},
                                    {"timeout": 4}, {"timeout": 121}, {"parallel_mode": "agentic"}])
def test_reject_unverified_config(kwargs):
    with pytest.raises(SearchError):
        SearchSettings(**kwargs)


@pytest.mark.parametrize("query,objective,key", [("", "", "key"), ("a" * 1001, "", "key"),
                                                 ("q", "a" * 4097, "key"), ("q", "", ""), ("q", "", "secret\n")])
def test_input_validation(query, objective, key):
    with pytest.raises(SearchError) as error:
        build_request(SearchSettings(), key, query, objective)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("provider,field", [("exa", "highlights"), ("parallel", "excerpts")])
def test_normalize_sources_skip_unsafe_and_deduplicate(provider, field):
    values = [{"url": "javascript:alert(1)"}, {"url": "file:///tmp/x"},
              {"url": "https://user:pass@example.com"}, {"url": "https://[broken"}]
    values.extend({"url": f"https://example.com/{i}", "title": f"Title {i}", field: ["a" * 2000]} for i in range(6))
    values.insert(5, values[4])
    result = parse_response(SearchSettings(provider), json.dumps({"results": values}).encode())
    assert [s.source_id for s in result.sources] == ["S1", "S2", "S3", "S4", "S5"]
    assert len({s.url for s in result.sources}) == 5
    assert all(len(s.excerpt) == 1200 for s in result.sources)


@pytest.mark.parametrize("value", [[], {}, {"results": None}, {"results": [None]},
                                    {"results": [{"url": "https://x.test", "excerpts": [1]}]}])
def test_protocol_error(value):
    with pytest.raises(SearchError):
        parse_response(SearchSettings(), json.dumps(value).encode())


def test_empty_and_large_response():
    assert parse_response(SearchSettings(), b'{"results":[]}').ok
    with pytest.raises(SearchError):
        parse_response(SearchSettings(), b" " * (MAX_RESPONSE_BYTES + 1))


@pytest.mark.parametrize("provider,field", [("exa", "highlights"), ("parallel", "excerpts")])
def test_ninety_nine_sources_are_not_clamped_to_five(provider, field):
    settings = SearchSettings(provider, 99)
    request = json.loads(build_request(settings, "fake-key", "query").payload)
    if provider == "exa":
        assert request['numResults'] == 99
    else:
        # Current Parallel v1 has no result count argument; never invent one.
        assert 'max_results' not in request and 'numResults' not in request
    entries = [{'url': f'https://x.test/{i}', 'title': f'Title {i}', field: ['excerpt']} for i in range(105)]
    result = parse_response(settings, json.dumps({'results': entries}).encode())
    assert len(result.sources) == 99 and result.sources[-1].source_id == 'S99'


def test_safe_http_errors():
    assert "密钥" in http_error(401)
    assert "额度" in http_error(402)
    assert "请求过多" in http_error(429)
    assert "重定向" in http_error(302)


class FakeSecurity:
    kSecClass = "class"
    kSecClassGenericPassword = "password"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecReturnData = "data_return"
    kSecMatchLimit = "limit"
    kSecMatchLimitOne = "one"
    kSecValueData = "data"
    kSecUseAuthenticationUI = "authentication_ui"
    kSecUseAuthenticationUIFail = "fail"
    errSecSuccess = 0
    errSecItemNotFound = -1

    def __init__(self):
        self.values = {}

    def SecItemCopyMatching(self, query, result):
        key = query["account"]
        return (0, self.values[key]) if key in self.values else (-1, None)

    def SecItemUpdate(self, query, values):
        if query["account"] not in self.values:
            return -1
        self.values[query["account"]] = values["data"]
        return 0

    def SecItemAdd(self, query, result):
        self.values[query["account"]] = query["data"]
        return 0, None

    def SecItemDelete(self, query):
        self.values.pop(query["account"], None)
        return 0


def test_keychain_create_update_delete_and_provider_isolation():
    store = SearchCredentials(FakeSecurity())
    assert store.get("exa") == ""
    store.set("exa", "key-exa")
    store.set("parallel", "key-parallel")
    store.set("exa", "new-exa")
    assert store.get("exa") == "new-exa"
    assert store.get("parallel") == "key-parallel"
    store.delete("exa")
    store.delete("exa")
    assert store.get("exa") == ""
    with pytest.raises(CredentialError):
        store.get("custom")


def test_keychain_failure_redacts_native_error(monkeypatch):
    backend = FakeSecurity()
    def fail(*args):
        raise RuntimeError("secret-value")
    monkeypatch.setattr(backend, "SecItemUpdate", fail)
    with pytest.raises(CredentialError) as error:
        SearchCredentials(backend).set("exa", "secret-value")
    assert "secret-value" not in str(error.value)


def test_background_keychain_read_disallows_interactive_prompt(monkeypatch):
    backend = FakeSecurity()
    queries = []
    monkeypatch.setattr(backend, 'SecItemCopyMatching', lambda query, _: (queries.append(query) or -1, None))
    assert SearchCredentials(backend).get('exa', interactive=False) == ''
    assert queries[0]['authentication_ui'] == 'fail'


def test_qt_search_request_and_result(qtbot, ollama_server, monkeypatch):
    from ai_desktop.services.qt_search import SearchJob
    from ai_desktop.services.web_search import ENDPOINTS
    monkeypatch.setitem(ENDPOINTS, "exa", ollama_server.url + "/search")
    ollama_server.enqueue({"results": [{"url": "https://python.org", "title": "Python", "highlights": ["docs"]}]})
    job = SearchJob(build_request(SearchSettings("exa"), "fake-key", "python docs"))
    with qtbot.waitSignal(job.finished, timeout=3000) as signal:
        job.start()
    assert signal.args[0].sources[0].url == "https://python.org"
    assert len(ollama_server.requests) == 1
    job.deleteLater()


def test_qt_search_cancel_is_single_terminal(qtbot, ollama_server, monkeypatch):
    from ai_desktop.services.qt_search import SearchJob
    from ai_desktop.services.web_search import ENDPOINTS
    monkeypatch.setitem(ENDPOINTS, "parallel", ollama_server.url + "/search")
    scenario = ollama_server.enqueue({"results": []}, hold_open=True)
    job = SearchJob(build_request(SearchSettings(), "fake-key", "python docs"))
    results = []
    job.finished.connect(results.append)
    job.start()
    qtbot.waitUntil(lambda: scenario.received.is_set(), timeout=3000)
    job.cancel()
    job.cancel()
    qtbot.waitUntil(lambda: scenario.disconnected.is_set(), timeout=3000)
    assert len(results) == 1 and results[0].cancelled
    job.deleteLater()


def test_qt_search_pre_cancel_does_not_send(qtbot, ollama_server, monkeypatch):
    from ai_desktop.services.qt_search import SearchJob
    from ai_desktop.services.web_search import ENDPOINTS
    monkeypatch.setitem(ENDPOINTS, "parallel", ollama_server.url + "/search")
    job = SearchJob(build_request(SearchSettings(), "fake-key", "python docs"))
    job.cancel()
    job.start()
    assert job.result.cancelled and not ollama_server.requests
    job.deleteLater()
