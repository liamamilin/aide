"""H3: real Qt HTTP through the shared worker, no paid API calls in tests."""
import json
import threading
import time
from dataclasses import replace

import pytest
from PyQt5.QtCore import QTimer

from ai_desktop.llm.chat_client import ChatClient
from ai_desktop.llm.events import ResultStatus
from ai_desktop.llm.ollama_protocol import ToolCall
from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolExecutionContext, ToolOutput
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.services.search_credentials import SearchCredentials
from ai_desktop.services.search_executor import SearchExecutor
from ai_desktop.services.web_search import ENDPOINTS, SearchResult, SearchSettings, SearchSource
from ai_desktop.ui import markdown
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.tool_card import ToolCard
from ai_desktop.utils.storage import Message
from tests.fake_ollama import FakeOllama


@pytest.fixture
def search_server(monkeypatch):
    server = FakeOllama()
    for provider in ENDPOINTS:
        monkeypatch.setitem(ENDPOINTS, provider, server.url + '/search')
    yield server
    server.close()


class Credentials:
    def __init__(self, key='test-only-key', error=False):
        self.key, self.error = key, error
        self.reads = []

    def get(self, provider, *, interactive, cancelled=None, deadline=None):
        self.reads.append((provider, interactive))
        if self.error:
            raise RuntimeError('secret-in-native-error')
        return self.key


def context():
    request = ChatClient().create_request([Message('user', 'search task')], '', agent_id='general_assistant')
    return ToolExecutionContext(request, ToolCall('call', 'web_search', '{"query":"python"}'), threading.Event())


@pytest.mark.parametrize('provider,field', [('parallel', 'excerpts'), ('exa', 'highlights')])
def test_executor_sources_are_unique_and_key_is_read_once(qapp, tmp_db, search_server, provider, field):
    credentials = Credentials()
    executor = SearchExecutor(SearchSettings(provider, 1), credentials=credentials)
    outputs = []
    for _ in range(2):
        search_server.enqueue({'results': [{'title': 'Python', 'url': 'https://docs.python.org/', field: ['docs']}]})
        output = executor({'query': 'python'}, context())
        assert not output.error and 'test-only-key' not in output.text
        outputs.append(json.loads(output.text))
    assert [value['sources'][0]['source_id'] for value in outputs] == ['S1', 'S2']
    assert set(executor.sources) == {'S1', 'S2'}
    assert credentials.reads == [(provider, False)]
    assert len(search_server.requests) == 2


@pytest.mark.parametrize('key,error,kind', [('', False, 'missing_credentials'),
                                         ('key', True, 'credentials_unavailable')])
def test_credentials_fail_without_http_or_secret_details(qapp, tmp_db, search_server, key, error, kind):
    executor = SearchExecutor(SearchSettings(), credentials=Credentials(key, error))
    for _ in range(2):
        result = executor({'query': 'python'}, context())
        assert result.error and json.loads(result.text)['error_type'] == kind
        assert 'secret-in-native-error' not in result.text
    assert not search_server.requests and len(executor._credentials.reads) == 1


@pytest.mark.parametrize('cancelled', [True, False])
def test_pre_cancel_or_limit_reads_no_key_and_sends_no_http(qapp, tmp_db, search_server, cancelled):
    credentials = Credentials()
    executor = SearchExecutor(SearchSettings(), credentials=credentials)
    ctx = context()
    if cancelled:
        ctx.cancelled.set()
    else:
        ctx = replace(ctx, deadline=time.monotonic() - 1)
    result = executor({'query': 'python'}, ctx)
    assert result.error
    assert json.loads(result.text)['error_type'] == ('cancelled' if cancelled else 'active_limit')
    assert not credentials.reads and not search_server.requests


@pytest.mark.parametrize('cancelled', [True, False])
def test_inflight_search_closes_on_cancel_and_deadline(qapp, tmp_db, search_server, cancelled):
    scenario = search_server.enqueue({'results': []}, before_headers=True)
    ctx = context()
    timer = QTimer()
    timer.setSingleShot(True)
    if cancelled:
        timer.timeout.connect(ctx.cancelled.set)
        timer.start(100)
    else:
        ctx = replace(ctx, deadline=time.monotonic() + .1)
    result = SearchExecutor(SearchSettings(), credentials=Credentials())({'query': 'python'}, ctx)
    timer.stop()
    assert result.error
    assert json.loads(result.text)['error_type'] == ('cancelled' if cancelled else 'active_limit')
    assert scenario.disconnected.wait(2) and len(search_server.requests) == 1


@pytest.mark.parametrize('status,kind', [(401, 'authentication'), (403, 'authentication'), (402, 'quota'),
                                      (429, 'rate_limit'), (302, 'redirect'), (500, 'http')])
def test_provider_errors_do_not_retry_or_fallback(qapp, tmp_db, search_server, status, kind):
    search_server.enqueue({'secret': 'not-shown'}, status=status)
    result = SearchExecutor(SearchSettings(), credentials=Credentials())({'query': 'python'}, context())
    assert result.error and json.loads(result.text)['error_type'] == kind
    assert 'not-shown' not in result.text and len(search_server.requests) == 1


@pytest.mark.parametrize('url', ['https://x.test/', 'https://x.test/' + 'x' * 3500])
def test_output_is_valid_bounded_json_with_intact_urls(url):
    executor = SearchExecutor(SearchSettings())
    source = SearchSource('S1', '中"\\' * 100, url, '中文"\\\n' * 500)
    result = executor._output(SearchResult((source,) * 5), time.monotonic())
    assert len(result.text.encode()) <= 4096
    data = json.loads(result.text)
    assert data['truncated'] and all(s['url'] == url for s in data['sources'])
    assert len(executor.sources) == len(data['sources'])
    assert [s['source_id'] for s in data['sources']] == [f'S{i+1}' for i in range(len(data['sources']))]


def test_unrepresentable_url_is_omitted_without_corrupting_it():
    executor = SearchExecutor(SearchSettings(), result_bytes=512)
    source = SearchSource('S1', 'Title', 'https://x.test/' + 'x' * 500, 'excerpt')
    data = json.loads(executor._output(SearchResult((source,)), time.monotonic()).text)
    assert data['sources'] == [] and data['truncated'] and not executor.sources


def test_large_source_limit_exceeds_five_and_reports_budget_omissions():
    executor = SearchExecutor(SearchSettings(max_results=99))
    sources = tuple(SearchSource(f'S{i+1}', f'Title {i}', f'https://x.test/{i}', 'excerpt') for i in range(99))
    output = executor._output(SearchResult(sources), time.monotonic())
    record = json.loads(output.text)
    assert 5 < len(record['sources']) < 99
    assert record['returned_sources'] == 99
    assert record['omitted_sources'] == 99 - len(record['sources'])
    assert record['truncated'] and len(output.text.encode()) <= 4096
    assert len(executor.sources) == len(record['sources'])


def test_config_snapshot_is_frozen_and_nonsecret(monkeypatch):
    from ai_desktop import config
    monkeypatch.setattr(config, 'SEARCH_PROVIDER', 'exa')
    snapshot = SearchSettings.from_config()
    monkeypatch.setattr(config, 'SEARCH_PROVIDER', 'parallel')
    assert snapshot.provider == 'exa' and 'key' not in json.dumps(snapshot.record())


@pytest.mark.parametrize('origin,agent,admitted', [('action', 'general_assistant', True),
    ('chat', '', True), ('chat', 'general_assistant', False)])
def test_action_missing_role_and_unadmitted_chat_never_construct_search(tmp_db, monkeypatch, origin, agent, admitted):
    def fail(*args, **kwargs):
        raise AssertionError('Unexpected Keychain read')
    monkeypatch.setattr(SearchCredentials, 'get', fail)
    worker = RunWorker([], '', agent_id=agent, origin=origin, tools_admitted=admitted,
                       search_settings=SearchSettings())
    assert not worker.context.tools and worker.context.search_settings is None and worker.search_executor is None
    worker.release_attachments()
    worker.deleteLater()


def make_worker(tmp_db, monkeypatch):
    monkeypatch.setattr(SearchCredentials, 'get', lambda *args, **kwargs: 'test-only-key')
    return RunWorker([Message('user', 'Find Python docs')], 'SYSTEM', agent_id='general_assistant',
                     think=False, options={'num_ctx': 8192, 'num_predict': 1024}, tools_admitted=True,
                     search_settings=SearchSettings('exa', 1))


def search_turn():
    return {'message': {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'provider-call', 'function': {'name': 'web_search', 'arguments': {'query': 'Python docs'}}}]},
        'done': True, 'done_reason': 'stop'}


def test_shared_worker_search_result_reaches_next_model(qtbot, tmp_db, monkeypatch, ollama_server, search_server):
    worker = make_worker(tmp_db, monkeypatch)
    ollama_server.enqueue(search_turn())
    search_server.enqueue({'results': [{'url': 'https://docs.python.org/', 'title': 'Python', 'highlights': ['Docs']}]})
    ollama_server.enqueue({'message': {'role': 'assistant', 'content': 'Python docs [S1]'}, 'done': True})
    events = []
    worker.run_event.connect(events.append)
    with qtbot.waitSignal(worker.done, timeout=5000) as signal:
        worker.start()
    assert worker.wait(2000)
    result = signal.args[0]
    assert result.status == ResultStatus.SUCCEEDED and result.text == 'Python docs [S1]'
    assert len(ollama_server.requests) == 2 and len(search_server.requests) == 1
    wire = ollama_server.requests[-1]['payload']
    tool_message = next(m for m in wire['messages'] if m['role'] == 'tool')
    assert json.loads(tool_message['content'])['sources'][0]['source_id'] == 'S1'
    assert 'test-only-key' not in json.dumps(wire)
    assert any(e.kind == RunEventKind.TOOL_UPDATED and e.status == 'searching' for e in events)
    assert worker.context.search_settings.provider == 'exa'
    worker.deleteLater()


def test_worker_cancel_search_has_no_next_model(qtbot, tmp_db, monkeypatch, ollama_server, search_server):
    worker = make_worker(tmp_db, monkeypatch)
    ollama_server.enqueue(search_turn())
    scenario = search_server.enqueue({'results': []}, before_headers=True)
    results = []
    worker.done.connect(results.append)
    worker.start()
    try:
        qtbot.waitUntil(scenario.received.is_set, timeout=3000)
        worker.cancel()
        qtbot.waitUntil(lambda: bool(results), timeout=3000)
        assert worker.wait(2000) and results[0].status == ResultStatus.CANCELLED
        assert scenario.disconnected.wait(2) and len(ollama_server.requests) == 1
    finally:
        worker.cancel()
        worker.wait(2000)
        worker.deleteLater()


def search_event(run='run', call='call'):
    return RunEvent(run, 1, 'step', 'request', 3, RunEventKind.TOOL_STARTED, call,
                    tool_name='web_search', arguments_json='{"query":"Python docs"}')


def source_record(error=''):
    return {'provider': 'exa', 'duration': .1, 'error_type': error, 'error': '', 'truncated': False,
            'sources': [{'source_id': 'S1', 'title': '<script>Plain title</script>',
                         'url': 'https://docs.python.org/', 'excerpt': 'Docs'}] if not error else []}


def test_source_card_and_answer_only_open_verified_sources(qtbot, monkeypatch):
    from ai_desktop import config
    dialog = ChatDialog(config.AGENTS, config.AGENTS[1])
    qtbot.addWidget(dialog)
    dialog.begin_assistant_stream()
    event = search_event()
    dialog.begin_tool_run(event.run_id)
    dialog.show_tool_event(event)
    dialog.show_tool_event(replace(event, kind=RunEventKind.TOOL_FINISHED,
                                  output=ToolOutput(json.dumps(source_record()))))
    card = dialog._tool_cards[('run', 'call')]
    assert card.status.text() == '找到 1 个来源' and len(card.source_buttons) == 1
    opened = []
    monkeypatch.setattr('ai_desktop.ui.tool_card.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    card.source_buttons[0].click()
    label = dialog._stream_bubble
    dialog.finalize_assistant_stream('Docs [S1], unknown [S2]', True)
    assert 'source://S1' in label.text() and 'source://S2' not in label.text()
    label.linkActivated.emit('source://S1')
    label.linkActivated.emit('source://S2')
    label.linkActivated.emit('https://malicious.test/')
    assert opened == ['https://docs.python.org/'] * 2
    dialog.refresh_theme()
    assert 'source://S1' in label.text()
    dialog.finish_tool_run()
    dialog.begin_assistant_stream()
    new_label = dialog._stream_bubble
    dialog.finalize_assistant_stream('Another [S1]', True)
    assert 'source://S1' not in new_label.text()
    # Prior answer retains its own map; new conversations clear it completely.
    label.linkActivated.emit('source://S1')
    assert len(opened) == 3
    dialog.clear_messages()
    assert dialog._run_sources == {}


def test_source_card_discloses_budget_omissions(qtbot):
    event = search_event()
    card = ToolCard(event)
    qtbot.addWidget(card)
    record = {**source_record(), 'returned_sources': 99, 'omitted_sources': 98, 'truncated': True}
    card.update_event(replace(event, kind=RunEventKind.TOOL_FINISHED,
                              output=ToolOutput(json.dumps(record))))
    assert card.status.text() == '保留 1 / 99 个来源'
    assert '上下文预算' in card.summary.text()


@pytest.mark.parametrize('kind,status', [('timeout', '搜索超时'), ('quota', '账户额度不足'),
                                       ('missing_credentials', '未配置密钥')])
def test_search_error_card(qtbot, kind, status):
    event = search_event()
    card = ToolCard(event)
    qtbot.addWidget(card)
    card.update_event(replace(event, kind=RunEventKind.TOOL_FINISHED,
                              output=ToolOutput(json.dumps(source_record(kind)), True)))
    assert card.status.text() == status and not card.source_buttons and card.terminal


def test_citations_ignore_code_html_and_unknown_ids():
    text = 'Text [S1] [S2]\n\n`inline [S1]`\n\n```\nfenced [S1]\n```\n\n<a href="https://bad.test">[S1]</a>'
    rendered, codes = markdown.to_html(text, sources={'S1': {}})
    assert rendered.count('href="source://S1"') == 2
    assert 'href="source://S2"' not in rendered and '<a href="https://bad.test"' not in rendered
    assert codes['copy://codeblock_0'] == 'fenced [S1]'


def test_citations_render_in_tables_headings_and_lists():
    rendered, _ = markdown.to_html('# Title [S1]\n\n- Bullet [S1]\n\n| A | B |\n| --- | --- |\n| One | [S1] |',
                                  sources={'S1': {}})
    assert rendered.count('href="source://S1"') == 3
