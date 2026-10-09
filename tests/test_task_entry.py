"""Explicit task grants, fresh loopback admission and real controller isolation."""
import time
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.events import ChatResult, ResultStatus
from ai_desktop.llm.model_options import global_options
from ai_desktop.llm.task_checks import TaskChecks
from ai_desktop.services import audit_store
from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot, load_workspace_policy
from ai_desktop.services.search_credentials import SearchCredentials
from ai_desktop.services.task_admission import (
    TASK_DIGEST,
    TASK_MODEL,
    TASK_SERVICE_VERSION,
    TaskAdmission,
    TaskAuthorization,
    local_service_url,
    validate_discovery,
)
from ai_desktop.services.web_search import SearchSettings
from ai_desktop.ui.fluent import MessageBox
from ai_desktop.ui.task_dialog import TaskDialog
from ai_desktop.ui.task_step_card import TaskStepCard
from ai_desktop.utils import storage
from tests import test_request_results as requests
from tests.test_search_execution import search_server as search_server

controller = requests.controller


@pytest.fixture(autouse=True)
def bounded_task_fixture(monkeypatch):
    # These tests exercise small tool workflows; product parameters are no
    # longer overridden by an independent 8192/1024 task profile.
    monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 8192)
    monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 1024)
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)


def discovery(model=TASK_MODEL):
    return ({'version': TASK_SERVICE_VERSION}, {'models': [{'name': model, 'digest': TASK_DIGEST}]},
            {'capabilities': ['completion', 'vision', 'thinking', 'tools'],
             'thinking': {'values': [False, True], 'default': True}})


def enqueue_discovery(server, values=None):
    for value in values or discovery():
        server.enqueue(value)


def authorize(controller, root, *, policy=BashPolicy.READONLY_AUTO, provider=None):
    controller._model = TASK_MODEL
    controller._dialog.refresh_models([TASK_MODEL])
    agent = next(agent for agent in controller._all_agents if agent.id == 'general_assistant')
    controller._active_agent = agent
    controller._dialog.set_active_agent(agent)
    execution = ExecutionSnapshot.create(root, policy) if root else None
    search = SearchSettings(provider=provider) if provider else None
    controller._task_authorization = TaskAuthorization(execution, search)
    controller._sync_action_context()


def call_response(command, content='我先读取文件。'):
    return {'message': {'content': content, 'tool_calls': [{'id': 'server-call', 'function':
            {'name': 'bash', 'arguments': {'command': command}}}]}, 'done': True, 'done_reason': 'stop'}


def answer(text='验证代码 AMBER-731。'):
    return {'message': {'content': text}, 'done': True, 'done_reason': 'stop'}


def idle(qtbot, ctl):
    qtbot.waitUntil(lambda: ctl._pending_task is None and ctl._worker is None and not ctl._stale_workers, timeout=5000)


@pytest.mark.parametrize('url', ['https://example.org', 'http://localhost@evil.test',
                              'http://user:pass@localhost:11434', 'http://localhost/path',
                              'http://localhost/?x=1', 'http://localhost/#token', 'http://localhost:999999'])
def test_admission_never_contacts_remote_or_credential_urls(qtbot, url):
    checks = TaskChecks(timeout_ms=100)
    with qtbot.waitSignal(checks.completed, timeout=500) as signal:
        checks.check(url)
    assert signal.args[1] is None and signal.args[2]


@pytest.mark.parametrize('change', ['version', 'digest', 'missing', 'duplicate', 'tools', 'context', 'invalid'])
def test_valid_identity_and_capabilities_required(change):
    version, tags, show = discovery()
    if change == 'version':
        version['version'] = ''
    elif change == 'digest':
        tags['models'][0]['digest'] = ''
    elif change == 'missing':
        tags['models'] = []
    elif change == 'duplicate':
        tags['models'] *= 2
    elif change == 'tools':
        show['capabilities'].remove('tools')
    elif change == 'context':
        show['model_info'] = {'test.context_length': 4096}
    else:
        tags = {'models': None}
    with pytest.raises(ValueError):
        validate_discovery('http://localhost:11434', version, tags, show)


def test_fresh_check_wire_order_and_valid_result(qtbot, ollama_server):
    checks = TaskChecks()
    enqueue_discovery(ollama_server)
    with qtbot.waitSignal(checks.completed, timeout=1500) as signal:
        sequence = checks.check(ollama_server.url)
    assert signal.args[0] == sequence and not signal.args[2]
    assert signal.args[1].valid(ollama_server.url)
    assert [req['path'] for req in ollama_server.requests] == ['/api/version', '/api/tags', '/api/show']
    assert ollama_server.requests[-1]['payload'] == {'model': TASK_MODEL}


@pytest.mark.parametrize('kind', ['timeout', 'http', 'json', 'oversize'])
def test_finite_check_failure_without_model_request(qtbot, ollama_server, kind):
    checks = TaskChecks(timeout_ms=100)
    if kind == 'timeout':
        ollama_server.enqueue(before_headers=True)
    elif kind == 'http':
        ollama_server.enqueue({}, status=302)
    elif kind == 'json':
        ollama_server.enqueue(chunks=[b'not json'])
    else:
        ollama_server.enqueue(chunks=[b'x'*(1024*1024+1)])
    with qtbot.waitSignal(checks.completed, timeout=1200) as signal:
        checks.check(ollama_server.url)
    assert signal.args[1] is None and signal.args[2]
    assert len(ollama_server.requests) == 1 and ollama_server.requests[0]['path'] == '/api/version'


def test_cancelled_check_cannot_deliver_or_continue(qtbot, ollama_server):
    checks = TaskChecks(timeout_ms=1000)
    scenario = ollama_server.enqueue(before_headers=True)
    events = []
    checks.completed.connect(lambda *args: events.append(args))
    checks.check(ollama_server.url)
    qtbot.waitUntil(scenario.received.is_set)
    checks.cancel()
    qtbot.waitUntil(scenario.disconnected.is_set)
    assert not events and not checks._active


def test_authorization_scope_expiry_workspace_and_exact_options(tmp_path):
    authorization = TaskAuthorization(ExecutionSnapshot.create(tmp_path))
    admission = validate_discovery('http://localhost:11434', *discovery())
    params = authorization.worker_kwargs(admission, admission.base_url, agent_id='general_assistant', origin='chat')
    assert params['options'] == global_options() and params['think'] is False and params['exact_options']
    for agent, origin in [('code_expert', 'chat'), ('general_assistant', 'action')]:
        with pytest.raises(ValueError):
            authorization.worker_kwargs(admission, admission.base_url, agent_id=agent, origin=origin)
    with pytest.raises(ValueError):
        authorization.worker_kwargs(replace(admission, checked_at=time.monotonic()-61), admission.base_url,
                                    agent_id='general_assistant', origin='chat')
    with pytest.raises(ValueError):
        authorization.worker_kwargs(admission, 'http://localhost:11435', agent_id='general_assistant', origin='chat')
    tmp_path.rename(tmp_path.with_name(tmp_path.name+'-gone'))
    with pytest.raises(ValueError):
        authorization.worker_kwargs(admission, admission.base_url, agent_id='general_assistant', origin='chat')


def test_dialog_opt_in_validates_and_remembers_policy(qtbot, tmp_db, tmp_path, ollama_server):
    dialog = TaskDialog(workspace_hint=str(tmp_path))
    qtbot.addWidget(dialog)
    assert not dialog._bash.isChecked() and not dialog._search.isChecked() and not dialog._enable.isEnabled()
    dialog._bash.setChecked(True)
    dialog._policy.setCurrentIndex(dialog._policy.findData(BashPolicy.CONFIRM_ALL.value))
    enqueue_discovery(ollama_server)
    dialog._start_check()
    qtbot.waitUntil(lambda: dialog.result() == dialog.Accepted)
    assert dialog.authorization.execution.policy == BashPolicy.CONFIRM_ALL
    assert load_workspace_policy(tmp_path) == BashPolicy.CONFIRM_ALL
    assert not dialog._checks._active


def test_dialog_failure_keeps_controls_and_does_not_save_grant(qtbot, tmp_db, tmp_path, ollama_server):
    dialog = TaskDialog(workspace_hint=str(tmp_path))
    qtbot.addWidget(dialog)
    dialog._bash.setChecked(True)
    values = list(discovery())
    values[0] = {'version': ''}
    enqueue_discovery(ollama_server, values)
    dialog._start_check()
    qtbot.waitUntil(lambda: not dialog._checking)
    assert dialog.authorization is None and dialog._enable.isEnabled()
    assert '版本' in dialog._status.text() and not storage.get_setting('execution_workspace')


def test_real_ui_entry_and_controller_tool_loop(qtbot, controller, tmp_path, ollama_server, monkeypatch):
    (tmp_path/'note.txt').write_text('AMBER-731')
    authorize(controller, tmp_path)
    original = (config.OLLAMA_NUM_CTX, config.OLLAMA_NUM_PREDICT, config.OLLAMA_THINK)
    monkeypatch.setattr(config, 'OLLAMA_TOP_K', 1)
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(call_response('cat note.txt'))
    ollama_server.enqueue(answer())
    controller._dialog.set_input_text('读取文件验证代码')
    controller._dialog._on_send()
    assert controller._pending_task is not None and controller._convo_id == 0
    idle(qtbot, controller)
    chats = [req['payload'] for req in ollama_server.requests if req['path'] == '/api/chat']
    assert len(chats) == 2 and chats[0]['model'] == TASK_MODEL
    assert chats[0]['think'] is False and chats[0]['options'] == global_options()
    assert [tool['function']['name'] for tool in chats[0]['tools']] == ['bash']
    assert chats[-1]['messages'][-1]['role'] == 'tool' and 'AMBER-731' in chats[-1]['messages'][-1]['content']
    assert original == (config.OLLAMA_NUM_CTX, config.OLLAMA_NUM_PREDICT, config.OLLAMA_THINK)
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['status'] == 'succeeded' and run['config']['admission']['digest'] == TASK_DIGEST
    cards = controller._dialog.findChildren(TaskStepCard)
    assert len(cards) == 2 and '读取文件' in cards[0].body.text() and cards[-1].final_answer
    assert 'AMBER-731' in cards[-1].body.text()
    assert cards[-1].regenerate.text() == '重新执行' and cards[-1].copy.isVisibleTo(controller._dialog)
    latest = controller._dialog._latest_finalized_assistant_buttons()
    assert latest[0] is cards[-1].regenerate


def test_failed_preflight_restores_draft_without_database_message(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    values = list(discovery())
    values[1] = {'models': []}
    enqueue_discovery(ollama_server, values)
    controller._on_user_message('keep this draft')
    idle(qtbot, controller)
    assert controller._dialog._input.toPlainText() == 'keep this draft'
    assert controller._convo_id == 0 and not storage.list_conversations()
    assert all(req['path'] != '/api/chat' for req in ollama_server.requests)


@pytest.mark.parametrize('cancel', ['stop', 'new', 'agent', 'settings', 'hidden', 'model'])
def test_pending_cancel_isolated_no_late_start(qtbot, controller, tmp_path, ollama_server, cancel):
    authorize(controller, tmp_path)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message('never execute')
    pending = controller._pending_task.copy()
    qtbot.waitUntil(scenario.received.is_set)
    if cancel == 'stop':
        controller._stop_worker()
    elif cancel == 'new':
        controller._new_conversation()
    elif cancel == 'agent':
        controller._on_agent_changed(config.AGENTS[1])
    elif cancel == 'settings':
        controller._clear_task_authorization()
    elif cancel == 'model':
        controller._on_model_changed('qwen3.8:27b-mlx')
    else:
        controller._on_dialog_closed()
    controller._on_task_checked(pending['sequence'], TaskAdmission(ollama_server.url, time.monotonic()), '')
    assert controller._pending_task is None and controller._worker is None and controller._convo_id == 0
    assert not storage.list_conversations()
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_action_does_not_inherit_active_task_authorization(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    ollama_server.enqueue(answer('翻译'))
    controller._on_action_requested('translate', 'material', 'current')
    idle(qtbot, controller)
    assert len(ollama_server.requests) == 1
    assert 'tools' not in ollama_server.requests[0]['payload']
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['origin'] == 'action' and run['config']['allowed_tools'] == []


def test_chat_regeneration_stays_no_tools_when_authorization_enabled(qtbot, controller, tmp_path, ollama_server):
    requests.send_and_wait(qtbot, controller, ollama_server, [answer('ordinary answer')])
    user = controller._messages[0]
    authorize(controller, tmp_path)
    ollama_server.enqueue(answer('regenerated'))
    controller._start_regeneration(user)
    idle(qtbot, controller)
    assert 'tools' not in ollama_server.requests[-1]['payload']


def test_task_rerun_requires_confirmation_new_identity_and_no_replay(qtbot, controller, tmp_path, ollama_server):
    (tmp_path/'note.txt').write_text('AMBER-731')
    authorize(controller, tmp_path)
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(call_response('cat note.txt'))
    ollama_server.enqueue(answer())
    controller._on_user_message('read')
    idle(qtbot, controller)
    user = controller._messages[0]
    first = storage.get_active_generation(user.id)
    with patch('ai_desktop.main.QMessageBox.question', return_value=MessageBox.No):
        controller._on_regenerate_requested()
    assert controller._pending_task is None
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(call_response('cat note.txt'))
    ollama_server.enqueue(answer('new answer'))
    with patch('ai_desktop.main.QMessageBox.question', return_value=MessageBox.Yes):
        controller._on_regenerate_requested()
    idle(qtbot, controller)
    latest = storage.get_active_generation(user.id)
    assert latest.config_snapshot['run_id'] != first.config_snapshot['run_id']
    assert len([message for message in controller._messages if message.role == 'user']) == 1
    chats = [req['payload'] for req in ollama_server.requests if req['path'] == '/api/chat']
    assert not any(message['role'] == 'tool' or 'tool_calls' in message for message in chats[2]['messages'])
    assert latest.answer == 'new answer'


def test_history_reload_requires_opt_in_preserves_workspace_hint(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer())
    controller._on_user_message('task question')
    idle(qtbot, controller)
    convo = controller._convo_id
    controller._new_conversation()
    controller._on_conversation_selected(convo)
    assert controller._task_authorization is None and controller._dialog._task_btn.text() == '工具：关闭'
    assert audit_store.list_runs(convo)[0]['config']['execution']['workspace'] == str(tmp_path)


def test_hidden_task_preflight_and_deleted_workspace_never_silent_chat_fallback(qtbot, controller, tmp_path):
    authorize(controller, tmp_path)
    admission = TaskAdmission(local_service_url(config.OLLAMA_BASE_URL), time.monotonic())
    tmp_path.rename(tmp_path.with_name(tmp_path.name+'-moved'))
    controller._on_user_message('keep', task_admission=admission)
    assert controller._worker is None and controller._convo_id == 0
    assert controller._dialog._input.toPlainText() == 'keep'


def test_cancelled_model_before_first_step_preserves_request_state(controller, tmp_path):
    authorize(controller, tmp_path)
    admission = TaskAdmission(local_service_url(config.OLLAMA_BASE_URL), time.monotonic())
    with patch('ai_desktop.main.RunWorker.start'):
        controller._on_user_message('task', task_admission=admission)
    worker = controller._worker
    worker._cancelled.set()
    result = ChatResult(worker.request.request_id, ResultStatus.CANCELLED, run_id=worker.request.run_id,
                        step_id=worker.request.step_id, conversation_id=worker.request.conversation_id)
    controller._on_stream_done(result)
    assert not controller._dialog._stream_timer.isActive()
    controller._stale_workers.remove(worker)
    worker.release_attachments()
    worker.deleteLater()


@pytest.mark.parametrize('provider,field', [('parallel', 'excerpts'), ('exa', 'highlights')])
def test_search_only_task_controller_http_and_history_sources(qtbot, controller, ollama_server,
                                                            search_server, monkeypatch, provider, field):
    monkeypatch.setattr(SearchCredentials, 'get', lambda *args, **kwargs: 'test-only-key')
    authorize(controller, None, provider=provider)
    enqueue_discovery(ollama_server)
    ollama_server.enqueue({'message': {'tool_calls': [{'function': {'name': 'web_search',
                          'arguments': {'query': 'official docs'}}}]}, 'done': True, 'done_reason': 'stop'})
    search_server.enqueue({'results': [{'title': 'Official documentation', 'url': 'https://docs.python.org/',
                                       field: ['Reference text']}]})
    ollama_server.enqueue(answer('See docs [S1]'))
    controller._on_user_message('明确搜索网页')
    idle(qtbot, controller)
    first = next(req['payload'] for req in ollama_server.requests if req['path'] == '/api/chat')
    assert [tool['function']['name'] for tool in first['tools']] == ['web_search']
    assert len(search_server.requests) == 1
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['config']['search']['provider'] == provider and run['config']['execution'] is None
    assert run['steps'][1]['tool_name'] == 'web_search'
    convo = controller._convo_id
    controller._on_conversation_selected(convo)
    labels = controller._dialog.findChildren(requests.QLabel, 'message_bubble')
    label = next(label for label in labels if getattr(label, '_markdown_source', '') == 'See docs [S1]')
    assert label._search_sources['S1']['url'] == 'https://docs.python.org/' and 'source://S1' in label.text()
    assert controller._task_authorization is None
    assert controller._dialog._latest_finalized_assistant_buttons()[0].text() == '重新执行'


@pytest.mark.parametrize('approve', [True, False])
def test_task_confirmation_buttons_execute_once_or_reject(qtbot, controller, tmp_path, ollama_server, approve):
    authorize(controller, tmp_path, policy=BashPolicy.CONFIRM_ALL)
    controller._dialog.command_decided.connect(controller._on_command_decided)
    controller._dialog.set_auto_hide(False)
    controller._dialog.show()
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(call_response('printf verified > created.txt'))
    ollama_server.enqueue(answer('done'))
    controller._on_user_message('write fixture')
    qtbot.waitUntil(lambda: any(card.pending for card in controller._dialog._tool_cards.values()), timeout=2500)
    card = next(card for card in controller._dialog._tool_cards.values() if card.pending)
    request = card.confirmation
    controller.float_btn.set_task_activity.assert_called_with('waiting')
    (card.approve if approve else card.reject).click()
    idle(qtbot, controller)
    assert (tmp_path/'created.txt').exists() is approve
    if approve:
        assert (tmp_path/'created.txt').read_text() == 'verified'
    assert not card.pending and card.terminal
    assert not controller._worker
    activities = [item.args[0] for item in controller.float_btn.set_task_activity.call_args_list]
    assert 'executing' in activities and activities[-1] == 'working'
    # A late click cannot repeat the side effect after the run closes.
    controller._on_command_decided(request, True)
    assert not card.approve.isEnabled()


def test_global_disable_closes_next_run_grant_and_persists(controller, tmp_path, monkeypatch):
    authorize(controller, tmp_path)
    monkeypatch.setattr(config, 'CHAT_TOOLS_ENABLED', True)
    controller._on_settings_applied({'task_tools_enabled': False})
    assert config.CHAT_TOOLS_ENABLED is False
    assert controller._task_authorization is None and '已禁用' in controller._dialog._task_btn.text()
    assert storage.get_setting('general_assistant_tools_enabled') == 'False'
    assert not controller._dialog._task_btn.isEnabled()
    assert controller._settings.apply({'task_tools_enabled': 'false'}) == []


def test_task_budget_change_invalidates_grant_and_records_next_run_limits(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'TASK_MAX_TOOL_CALLS', 16)
    authorize(controller, tmp_path)
    controller._on_settings_applied({'task_max_tool_calls': 39})
    assert controller._task_authorization is None
    assert storage.get_setting('task_max_tool_calls') == '39'
    from ai_desktop.llm.run_worker import RunWorker
    from ai_desktop.services.run_audit import RunAudit
    convo = storage.create_conversation('general_assistant', 'budget fixture')
    user = storage.save_message(convo.id, 'user', 'task')
    worker = RunWorker([user], 'SYSTEM',
                       conversation_id=convo.id, agent_id='general_assistant', tools_admitted=True,
                       execution=ExecutionSnapshot.create(tmp_path))
    audit = RunAudit(worker.context, user.id)
    assert audit_store.list_runs(convo.id)[0]['config']['limits']['max_tool_calls'] == 39
    audit.deleteLater()
    worker.deleteLater()


def test_preflight_attachment_is_pinned_then_restored_and_released(qtbot, controller, tmp_path, ollama_server):
    from PyQt5.QtGui import QImage
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(0xffffff)
    original = tmp_path/'fixture.png'
    assert image.save(str(original))
    controller._dialog.attach_image_paths([str(original)])
    path = controller._dialog.get_pending_images()[0]
    authorize(controller, tmp_path)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._dialog.set_input_text('image draft')
    controller._dialog._on_send()
    qtbot.waitUntil(scenario.received.is_set)
    retained = controller._pending_task['retained']
    assert retained and all(storage._active_attachment_uses[key] >= 1 for key in retained)
    controller._stop_worker()
    assert controller._dialog.get_pending_images() == [path]
    assert all(key not in storage._active_attachment_uses for key in retained)
    assert controller._dialog._input.toPlainText() == 'image draft'
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_authorization_remains_for_followups_in_same_conversation(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    grant = controller._task_authorization
    conversation = None
    for text in ['first task', 'followup task']:
        enqueue_discovery(ollama_server)
        ollama_server.enqueue(answer('done'))
        controller._on_user_message(text)
        idle(qtbot, controller)
        conversation = conversation or controller._convo_id
        assert controller._convo_id == conversation and controller._task_authorization is grant
    runs = audit_store.list_runs(conversation)
    assert len(runs) == 2 and all(run['config']['allowed_tools'] == ['bash'] for run in runs)
    assert controller._dialog._task_btn.text() == '工具：已启用'


def test_selector_change_keeps_grant_and_tool_requests_use_27b(qtbot, controller, tmp_path, ollama_server):
    model = 'qwen3.8:27b-mlx'
    (tmp_path/'note.txt').write_text('AMBER-731')
    authorize(controller, tmp_path)
    grant = controller._task_authorization
    controller._dialog.model_changed.connect(controller._on_model_changed)
    controller._dialog.refresh_models([TASK_MODEL, model])
    controller._dialog._model_combo.setCurrentText(model)
    assert controller._model == model and controller._task_authorization is grant
    assert model in controller._dialog._task_status.text()
    assert controller._dialog._task_btn.text() == '工具：已启用'
    enqueue_discovery(ollama_server, discovery(model))
    ollama_server.enqueue(call_response('cat note.txt'))
    ollama_server.enqueue(answer('AMBER-731'))
    controller._on_user_message('read note.txt')
    idle(qtbot, controller)
    chats = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(chats) == 2 and all(r['model'] == model for r in chats)
    assert all(r['tools'][0]['function']['name'] == 'bash' for r in chats)
    assert next(r['payload'] for r in ollama_server.requests if r['path'] == '/api/show') == {'model': model}
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['config']['model'] == model and run['config']['admission']['model'] == model
    assert run['config']['admission']['budget_verified'] is False


def test_task_panel_checks_selected_27b(qtbot, ollama_server):
    model = 'qwen3.8:27b-mlx'
    panel = TaskDialog(model=model)
    qtbot.addWidget(panel)
    panel._search.setChecked(True)
    enqueue_discovery(ollama_server, discovery(model))
    panel._enable.click()
    qtbot.waitUntil(lambda: panel.result() == panel.Accepted, timeout=2500)
    assert panel.authorization.search and panel.admission.model == model
    assert ollama_server.requests[-1]['payload'] == {'model': model}


def test_unsupported_selected_model_preserves_draft_no_9b_fallback(qtbot, controller, tmp_path, ollama_server):
    model = 'qwen3.8:27b-mlx'
    authorize(controller, tmp_path)
    controller._on_model_changed(model)
    metadata = list(discovery(model))
    metadata[2]['capabilities'].remove('tools')
    enqueue_discovery(ollama_server, metadata)
    with patch.object(controller, '_show_notice') as notice:
        controller._on_user_message('keep draft')
        idle(qtbot, controller)
    assert model in notice.call_args.args[2]
    assert controller._dialog._input.toPlainText() == 'keep draft'
    assert not storage.list_conversations()
    assert all(r['path'] != '/api/chat' for r in ollama_server.requests)
    assert ollama_server.requests[-1]['payload']['model'] == model


def test_admission_for_old_model_cannot_start_new_model(controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    grant = controller._task_authorization
    controller._on_model_changed('qwen3.8:27b-mlx')
    admission = TaskAdmission(ollama_server.url, time.monotonic())
    with patch.object(controller, '_show_notice'):
        controller._on_user_message('keep draft', task_admission=admission)
    assert controller._worker is None and controller._pending_task is None
    assert controller._task_authorization is grant and not storage.list_conversations()
    assert controller._dialog._input.toPlainText() == 'keep draft'
    assert not ollama_server.requests


def test_model_change_cancels_pending_command_and_late_approval(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path, policy=BashPolicy.CONFIRM_ALL)
    grant = controller._task_authorization
    controller._dialog.command_decided.connect(controller._on_command_decided)
    controller._dialog.set_auto_hide(False)
    controller._dialog.show()
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(call_response('printf stale > stale.txt'))
    controller._on_user_message('pending write')
    qtbot.waitUntil(lambda: any(card.pending for card in controller._dialog._tool_cards.values()), timeout=2500)
    card = next(card for card in controller._dialog._tool_cards.values() if card.pending)
    request = card.confirmation
    controller._on_model_changed('qwen3.8:27b-mlx')
    controller._on_command_decided(request, True)
    idle(qtbot, controller)
    assert not (tmp_path/'stale.txt').exists()
    assert controller._task_authorization is grant and not card.pending
    assert audit_store.list_runs(controller._convo_id)[0]['status'] == 'cancelled'


@pytest.mark.parametrize('think', [False, None])
def test_dynamic_admission_uses_current_identity_and_thinking(think):
    model = 'new-model'
    version, tags, show = discovery(model)
    version['version'] = 'new-service-version'
    tags['models'][0]['digest'] = 'new-model-digest'
    if think is None:
        show.pop('thinking')
    admission = validate_discovery('http://localhost:11434', version, tags, show, model=model)
    grant = TaskAuthorization(search=SearchSettings(provider='exa'))
    params = grant.worker_kwargs(admission, admission.base_url, agent_id='general_assistant',
                                 origin='chat', model=model)
    assert params['model'] == model and params['think'] is think
    assert admission.digest == 'new-model-digest' and admission.service_version == 'new-service-version'
    assert not admission.record()['budget_verified']
    with pytest.raises(ValueError):
        grant.worker_kwargs(admission, admission.base_url, agent_id='general_assistant',
                            origin='chat', model=TASK_MODEL)
