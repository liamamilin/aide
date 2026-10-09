"""Opt-in live diagnostics stay bounded; ordinary tests never use real keys."""
import json
from dataclasses import replace

import pytest
from PyQt5.QtWidgets import QLabel

from ai_desktop.diagnostics import harness_acceptance as diagnostic
from ai_desktop.diagnostics.search_acceptance import SearchTrace, validate_paid_case
from ai_desktop.llm.run_types import RunEventKind
from ai_desktop.services import audit_store
from ai_desktop.services.qt_search import SearchJob
from ai_desktop.services.search_executor import SearchExecutor
from ai_desktop.services.web_search import (
    MAX_RUN_SOURCE_ID,
    SearchResult,
    SearchSettings,
    SearchSource,
    build_request,
    normalized_sources,
)
from ai_desktop.ui.tool_card import ToolCard
from ai_desktop.utils import storage
from scripts import harness_acceptance as launcher
from tests.test_agent_tools import grant
from tests.test_request_results import controller as controller
from tests.test_run_audit import event, generation, new_run
from tests.test_search_execution import search_event, search_turn
from tests.test_search_execution import search_server as search_server
from tests.test_task_entry import answer, discovery, enqueue_discovery, idle


@pytest.mark.parametrize('case,allowed', [('search_parallel', False), ('search_exa', False),
                                        (None, True), ('bash_readonly', True), ('search_exa', 'true')])
def test_live_search_requires_explicit_case_and_flag(case, allowed):
    with pytest.raises(ValueError):
        validate_paid_case(case, allowed)


@pytest.mark.parametrize('case,allowed', [(None, False), ('bash_readonly', False),
                                        ('search_parallel', True), ('search_exa', True)])
def test_default_cases_remain_free(case, allowed):
    validate_paid_case(case, allowed)


def test_missing_paid_flag_rejected_before_initialization(monkeypatch):
    monkeypatch.setattr(diagnostic, 'acceptance', lambda *a, **k: pytest.fail('Must not initialize Qt or credentials'))
    with pytest.raises(SystemExit) as failure:
        diagnostic.main(['--only', 'search_exa'])
    assert failure.value.code == 2


def test_launcher_forwards_explicit_paid_case_and_role(monkeypatch):
    seen = []
    def run(command, **kwargs):
        from types import SimpleNamespace
        seen.extend(command)
        assert kwargs['env']['AIDE_DATA_DIR'] != str(storage.DB_PATH.parent)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    launcher.main(['--only', 'search_parallel', '--allow-paid-search', '--search-agent', 'acceptance-researcher'])
    assert '--allow-paid-search' in seen and seen[-2:] == ['--search-agent', 'acceptance-researcher']


@pytest.mark.parametrize('status', [200, 429])
def test_diagnostic_traces_safe_metadata_and_blocks_second_http(qtbot, search_server, status):
    search_server.enqueue({'results': [{'url': 'https://docs.python.org/', 'highlights': ['Docs']}]}, status=status)
    original = SearchJob.start
    first = SearchJob(build_request(SearchSettings('exa', 1), 'secret-test-key', 'private-test-query'))
    second = SearchJob(build_request(SearchSettings('exa', 1), 'secret-test-key', 'private-test-query'))
    with SearchTrace() as trace:
        with qtbot.waitSignal(first.finished, timeout=2000):
            first.start()
        with qtbot.waitSignal(second.finished, timeout=500):
            second.start()
        assert second.result.error_type == 'acceptance_limit'
        assert trace.attempts == 1 and len(search_server.requests) == 1
        assert trace.results == [{'provider': 'exa', 'http_status': status,
                                 'error_type': '' if status == 200 else 'rate_limit',
                                 'source_count': 1 if status == 200 else 0}]
        encoded = json.dumps(trace.results)
        assert 'secret-test-key' not in encoded and 'private-test-query' not in encoded
    assert SearchJob.start is original
    first.deleteLater()
    second.deleteLater()


def test_diagnostic_trace_restores_network_on_exception():
    original = SearchJob.start
    with pytest.raises(RuntimeError), SearchTrace():
        raise RuntimeError('test')
    assert SearchJob.start is original


def test_cancel_with_already_deleted_reply_emits_once_and_releases_waiter(qtbot, search_server):
    from PyQt5 import sip
    scenario = search_server.enqueue({}, before_headers=True)
    job = SearchJob(build_request(SearchSettings('exa', 1), 'test-key', 'docs'))
    finished = []
    job.finished.connect(finished.append)
    job.start()
    qtbot.waitUntil(scenario.received.is_set)
    # Model a delayed destroyed notification; cancellation must also handle
    # a native reply that has already gone before its callback arrives.
    job._reply.destroyed.disconnect(job._reply_destroyed)
    sip.delete(job._manager)  # Native ownership destroys its network reply.
    assert sip.isdeleted(job._reply)
    with qtbot.waitSignal(job.finished, timeout=500):
        job.cancel()
    job.cancel()
    assert job.result.cancelled and job._reply is None and len(finished) == 1
    assert not job._timer.isActive() and scenario.disconnected.wait(2)
    job.deleteLater()


def sources(start=1, count=24):
    return tuple(SearchSource(f'S{i}', 'Python', f'https://docs.python.org/{i}', '')
                 for i in range(start, start+count))


def test_card_and_history_keep_more_than_five_and_sources_after_s30(qtbot, tmp_db):
    executor = SearchExecutor(SearchSettings('exa', 99))
    outputs = [executor._output(SearchResult(sources()), 0), executor._output(SearchResult(sources(25)), 0)]
    ctx, user = new_run(tool='web_search')
    audit_store.observe(event(ctx, RunEventKind.MODEL_STARTED, 1))
    audit_store.observe(event(ctx, RunEventKind.MODEL_FINISHED, 2, status='succeeded'))
    for index, output in enumerate(outputs):
        call = 'search-'+str(index)
        audit_store.observe(event(ctx, RunEventKind.TOOL_STARTED, 3+index*2, call=call))
        audit_store.observe(event(ctx, RunEventKind.TOOL_FINISHED, 4+index*2, call=call,
                                  output=output, status='succeeded'))
        record = json.loads(output.text)
        assert len(record['sources']) == 24 and len(output.text.encode()) <= 4096
        card_event = search_event(call=call)
        card = ToolCard(card_event)
        qtbot.addWidget(card)
        card.update_event(replace(card_event, kind=RunEventKind.TOOL_FINISHED, output=output))
        assert card.status.text() == '找到 24 个来源' and len(card.source_buttons) == 24
    audit_store.observe(event(ctx, RunEventKind.FINISHED, 7, status='succeeded'))
    gen, _ = generation(ctx, user, 'Sources [S6] [S31] [S48]')
    restored = audit_store.sources_for_message(generation_id=gen.id)
    assert len(restored) == 48 and restored['S48']['url'] == 'https://docs.python.org/48'


@pytest.mark.parametrize('identity', ['S0', 'S01', 'S9802', 'S10000', 'S-1', 'Sabc', 'S1\n', 1])
def test_source_validation_rejects_unbounded_or_noncanonical_ids(identity):
    source = {'source_id': identity, 'title': 'Docs', 'url': 'https://docs.python.org/'}
    assert not normalized_sources({'provider': 'exa', 'sources': [source]})


def test_sources_are_bounded_and_duplicate_ids_do_not_change_links():
    source = {'source_id': f'S{MAX_RUN_SOURCE_ID}', 'title': 'Docs', 'url': 'https://docs.python.org/'}
    duplicate = {**source, 'url': 'https://different.test/'}
    values = normalized_sources({'provider': 'exa', 'sources': [source, duplicate]})
    assert values == [source]
    assert len(normalized_sources({'provider': 'parallel', 'sources': [
        {**source, 'source_id': f'S{i}'} for i in range(1, 101)]})) == 99


@pytest.fixture(autouse=True)
def bounded_parameters(monkeypatch):
    from ai_desktop import config
    from ai_desktop.main import ChatController
    monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 8192)
    monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 1024)
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    monkeypatch.setattr(config, 'CHAT_TOOLS_ENABLED', True)
    monkeypatch.setattr(ChatController, '_show_notice', lambda *a: None)


@pytest.mark.parametrize('provider', ['parallel', 'exa'])
@pytest.mark.parametrize('operation', ['stop', 'new', 'agent', 'hide', 'model'])
def test_controller_cancels_inflight_search_without_late_answer(qtbot, controller, tmp_path,
                                                              ollama_server, search_server, monkeypatch,
                                                              provider, operation):
    from ai_desktop.services.search_credentials import SearchCredentials
    monkeypatch.setattr(SearchCredentials, 'get', lambda *a, **k: 'test-only-key')
    grant(controller, 'code_expert', provider=provider)
    controller._dialog.set_auto_hide(False)
    controller._dialog.tool_stop_requested.connect(controller._on_tool_stop_requested)
    controller._dialog.closed.connect(controller._on_dialog_closed)
    controller._dialog.show()
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(search_turn())
    scenario = search_server.enqueue({'results': []}, before_headers=True)
    controller._on_user_message('search docs')
    qtbot.waitUntil(scenario.received.is_set, timeout=3000)
    qtbot.waitUntil(lambda: any(c.tool_name == 'web_search' and c.status.text() == '正在搜索'
                               for c in controller._dialog._tool_cards.values()), timeout=3000)
    card = next(c for c in controller._dialog._tool_cards.values() if c.tool_name == 'web_search')
    assert card.status.text() == '正在搜索'
    convo = controller._convo_id
    if operation == 'stop':
        controller._on_stop_requested()
    elif operation == 'new':
        controller._new_conversation()
    elif operation == 'agent':
        controller._on_tray_agent(next(a for a in controller._all_agents if a.id == 'translator'))
    elif operation == 'model':
        controller._on_model_changed('qwen3.8:27b-mlx')
    else:
        controller._dialog.hide()
    idle(qtbot, controller)
    assert scenario.disconnected.wait(2)
    assert len(search_server.requests) == 1
    assert len([r for r in ollama_server.requests if r['path'] == '/api/chat']) == 1
    run = audit_store.list_runs(convo)[0]
    assert run['status'] == 'cancelled'
    assert not any(m.role == 'assistant' for m in storage.get_conversation(convo).messages)


@pytest.mark.parametrize('provider', ['parallel', 'exa'])
@pytest.mark.parametrize('status,kind', [(401, 'authentication'), (429, 'rate_limit'), (500, 'http')])
def test_controller_error_feedback_no_fallback_or_fake_citation(qtbot, controller, ollama_server,
                                                              search_server, monkeypatch, provider, status, kind):
    from ai_desktop.services.search_credentials import SearchCredentials
    monkeypatch.setattr(SearchCredentials, 'get', lambda *a, **k: 'test-only-key')
    grant(controller, 'code_expert', provider=provider)
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(search_turn())
    search_server.enqueue({}, status=status)
    ollama_server.enqueue(answer('搜索失败 [S1]'))
    controller._on_user_message('search docs')
    idle(qtbot, controller)
    assert len(search_server.requests) == 1
    run = audit_store.list_runs(controller._convo_id)[0]
    output = json.loads(run['steps'][1]['payload']['output'])
    assert output['error_type'] == kind and not output['sources']
    card = next(c for c in controller._dialog._tool_cards.values() if c.tool_name == 'web_search')
    assert card.terminal and not card.source_buttons
    label = next(label for label in controller._dialog.findChildren(QLabel)
                 if getattr(label, '_markdown_source', '') == '搜索失败 [S1]')
    assert 'source://S1' not in label.text() and not label._search_sources


@pytest.mark.parametrize('operation', ['stop', 'new'])
def test_controller_cancels_blocked_credential_child_before_http(qtbot, controller, ollama_server,
                                                               search_server, monkeypatch, operation):
    import subprocess
    import sys

    from ai_desktop.services import credential_reader
    processes = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(credential_reader, 'reader_command', lambda: [sys.executable, '-c',
                                                                    'import time; time.sleep(60)'])
    monkeypatch.setattr(credential_reader.subprocess, 'Popen', capture)
    grant(controller, 'code_expert', provider='exa')
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(search_turn())
    controller._on_user_message('search docs')
    qtbot.waitUntil(lambda: len(processes) == 1, timeout=3000)
    convo = controller._convo_id
    if operation == 'stop':
        controller._on_stop_requested()
    else:
        controller._new_conversation()
    idle(qtbot, controller)
    assert processes[0].poll() is not None and not search_server.requests
    assert audit_store.list_runs(convo)[0]['status'] == 'cancelled'
    assert not any(m.role == 'assistant' for m in storage.get_conversation(convo).messages)
