"""Tools keep role prompts and require explicit authorization for that role."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.main import ChatController
from ai_desktop.services import audit_store
from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.services.search_credentials import SearchCredentials
from ai_desktop.services.task_admission import TaskAuthorization
from ai_desktop.services.web_search import SearchSettings
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.ui.fluent import MessageBox
from ai_desktop.ui.task_dialog import TaskDialog
from ai_desktop.utils import storage
from tests.test_request_results import controller as controller
from tests.test_search_execution import search_server as search_server
from tests.test_search_execution import search_turn
from tests.test_task_entry import answer, call_response, discovery, enqueue_discovery, idle

ROLE_IDS = [agent.id for agent in config.AGENTS] + ['custom-researcher']


@pytest.fixture(autouse=True)
def parameters(monkeypatch):
    monkeypatch.setattr(config, 'CHAT_TOOLS_ENABLED', True)
    monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 8192)
    monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 1024)
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    # A failing assertion must report the underlying preflight error rather
    # than wait for a human to dismiss an offscreen modal notice.
    monkeypatch.setattr(ChatController, '_show_notice', lambda *args: None)


def select(ctl, agent_id):
    if agent_id.startswith('custom-'):
        ctl._on_custom_agents_saved([{'id': agent_id, 'name': '资料研究', 'icon': '📚',
                                     'system_prompt': '你是资料研究助手，回答保留原文关键结论。'}])
    agent = next(a for a in ctl._all_agents if a.id == agent_id)
    ctl._dialog.set_active_agent(agent)
    ctl._on_agent_changed(agent)
    return agent


def grant(ctl, agent_id, root=None, provider=None, policy=BashPolicy.READONLY_AUTO):
    agent = select(ctl, agent_id)
    ctl._task_authorization = TaskAuthorization(
        ExecutionSnapshot.create(root, policy) if root else None,
        SearchSettings(provider=provider) if provider else None, agent_id=agent.id)
    ctl._sync_action_context()
    return agent


@pytest.mark.parametrize('agent_id', ROLE_IDS)
def test_all_roles_real_bash_loop_preserves_prompt_and_retry(qtbot, controller, tmp_path, ollama_server, agent_id):
    (tmp_path/'note.txt').write_text('AMBER-731')
    agent = grant(controller, agent_id, tmp_path)
    controller._dialog.show()
    assert controller._dialog._task_row.isVisible() and controller._dialog._task_btn.isEnabled()
    for retry in (False, True):
        enqueue_discovery(ollama_server, discovery(controller._model))
        ollama_server.enqueue(call_response('cat note.txt'))
        ollama_server.enqueue(answer())
        if retry:
            with patch.object(MessageBox, 'question', return_value=MessageBox.Yes):
                controller._on_regenerate_requested()
        else:
            controller._on_user_message('读取 note.txt')
        idle(qtbot, controller)
    chats = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(chats) == 4
    for payload in chats:
        assert payload['messages'][0]['content'].startswith(agent.system_prompt + '\n\n[工具使用规则]')
        assert '不要只翻译、改写工具请求本身' in payload['messages'][0]['content']
        assert [t['function']['name'] for t in payload['tools']] == ['bash']
        assert payload['model'] == controller._model
    runs = audit_store.list_runs(controller._convo_id)
    assert len(runs) == 2 and all(r['agent_id'] == agent_id and r['status'] == 'succeeded' for r in runs)
    assert len([m for m in controller._messages if m.role == 'user']) == 1


@pytest.mark.parametrize('agent_id', ROLE_IDS)
@pytest.mark.parametrize('provider,field', [('parallel', 'excerpts'), ('exa', 'highlights')])
def test_all_roles_search_sources(qtbot, controller, ollama_server, search_server, monkeypatch,
                                 agent_id, provider, field):
    monkeypatch.setattr(SearchCredentials, 'get', lambda *args, **kwargs: 'test-only-key')
    agent = grant(controller, agent_id, provider=provider)
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(search_turn())
    search_server.enqueue({'results': [{'title': 'Python', 'url': 'https://docs.python.org/', field: ['Docs']}]})
    ollama_server.enqueue(answer('Python docs [S1]'))
    controller._on_user_message('搜索 Python 官方文档')
    idle(qtbot, controller)
    chats = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(chats) == 2 and len(search_server.requests) == 1
    assert chats[0]['messages'][0]['content'].startswith(agent.system_prompt)
    assert '[S1]' in chats[0]['messages'][0]['content']
    assert [t['function']['name'] for t in chats[0]['tools']] == ['web_search']
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['agent_id'] == agent_id and run['config']['search']['provider'] == provider
    assert run['steps'][1]['tool_name'] == 'web_search' and run['status'] == 'succeeded'


@pytest.mark.parametrize('agent_id', ROLE_IDS)
def test_task_panel_grants_actual_agent(qtbot, controller, tmp_path, ollama_server, agent_id):
    agent = select(controller, agent_id)
    enqueue_discovery(ollama_server, discovery(controller._model))
    def configure(panel):
        assert panel._agent_id == agent.id and panel.authorization is None
        panel._workspace.setText(str(tmp_path))
        panel._bash.setChecked(True)
        panel._start_check()
        qtbot.waitUntil(lambda: panel.result() == panel.Accepted)
        return panel.result()
    with patch.object(TaskDialog, 'exec_', configure):
        controller._on_task_settings_requested()
    assert controller._task_authorization.agent_id == agent_id
    assert controller._task_authorization.record()['agent_id'] == agent_id


@pytest.mark.parametrize('via', ['selector', 'tray', 'visible', 'custom-edit'])
def test_switch_role_cancels_pending_command_and_late_approval(qtbot, controller, tmp_path, ollama_server, via):
    grant(controller, 'custom-researcher', tmp_path, policy=BashPolicy.CONFIRM_ALL)
    controller._dialog.show()
    controller._dialog.set_auto_hide(False)
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(call_response('printf stale > stale.txt'))
    controller._on_user_message('write')
    qtbot.waitUntil(lambda: any(c.pending for c in controller._dialog._tool_cards.values()), timeout=2500)
    card = next(c for c in controller._dialog._tool_cards.values() if c.pending)
    confirmation = card.confirmation
    other = next(a for a in controller._all_agents if a.id == 'translator')
    if via == 'selector':
        controller._dialog.set_active_agent(other)
        controller._on_agent_changed(other)
    elif via == 'tray':
        controller._on_tray_agent(other)
    elif via == 'visible':
        controller._dialog.set_active_agent(other)
        controller._sync_active_agent_from_dialog()
    else:
        controller._on_custom_agents_saved([])
    controller._on_command_decided(confirmation, True)
    idle(qtbot, controller)
    assert controller._task_authorization is None and not card.pending
    assert not (tmp_path/'stale.txt').exists()
    assert audit_store.list_runs(controller._convo_id)[0]['status'] == 'cancelled'


def test_stale_other_role_grant_rejected_before_discovery(controller, tmp_path, ollama_server):
    grant(controller, 'code_expert', tmp_path)
    controller._active_agent = next(a for a in controller._all_agents if a.id == 'translator')
    controller._dialog.set_active_agent(controller._active_agent)
    with patch.object(controller, '_show_notice'):
        controller._on_user_message('keep material')
    assert not ollama_server.requests and not storage.list_conversations()
    assert controller._dialog._input.toPlainText() == 'keep material'


def test_panel_does_not_prefill_other_role_authorization(qtbot, tmp_db, tmp_path):
    old = TaskAuthorization(ExecutionSnapshot.create(tmp_path), agent_id='code_expert')
    panel = TaskDialog(old, agent_id='translator')
    qtbot.addWidget(panel)
    assert panel.authorization is None and not panel._bash.isChecked() and not panel._search.isChecked()


@pytest.mark.parametrize('agent_id', ROLE_IDS)
def test_role_profile_parameters_and_model_switch(qtbot, controller, tmp_path, ollama_server, agent_id):
    agent = grant(controller, agent_id, tmp_path)
    authorization = controller._task_authorization
    controller._profile_mgr.save(ModelProfile('role', '专用参数', 'profile-model', False, 0.4, 1500))
    controller._active_agent = replace(agent, profile_id='role')
    controller._dialog.set_active_agent(controller._active_agent)
    controller._on_model_changed('qwen3.8:27b-mlx')
    assert controller._task_authorization is authorization
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(answer('profile result'))
    controller._on_user_message('question')
    idle(qtbot, controller)
    payload = ollama_server.requests[-1]['payload']
    assert payload['model'] == 'qwen3.8:27b-mlx' and payload['think'] is False
    assert payload['options']['num_predict'] == 1500 and payload['options']['temperature'] == 0.4
    assert payload['messages'][0]['content'].startswith(agent.system_prompt)
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['agent_id'] == agent_id and run['config']['think_source'] == '专用参数'


@pytest.mark.parametrize('agent_id', ['code_expert', 'translator', 'custom-researcher'])
def test_role_switch_discards_inflight_discovery(qtbot, controller, tmp_path, ollama_server, agent_id):
    grant(controller, agent_id, tmp_path)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message('keep draft')
    qtbot.waitUntil(scenario.received.is_set)
    pending = controller._pending_task.copy()
    select(controller, 'summarizer')
    controller._on_task_checked(pending['sequence'], None, '')
    idle(qtbot, controller)
    assert controller._task_authorization is None and not storage.list_conversations()
    assert controller._dialog._input.toPlainText() == 'keep draft'
    assert all(r['path'] != '/api/chat' for r in ollama_server.requests)


@pytest.mark.parametrize('agent_id', ['code_expert', 'translator', 'custom-researcher'])
def test_current_action_does_not_inherit_role_tool_grant(qtbot, controller, tmp_path, ollama_server, agent_id):
    grant(controller, agent_id, tmp_path, provider='exa')
    # With no saved conversation the product deliberately treats "current"
    # as a new Action conversation, which clears grants by design.
    controller._convo_id = storage.create_conversation(agent_id, 'current fixture').id
    authorization = controller._task_authorization
    ollama_server.enqueue(answer('translated'))
    controller._on_action_requested('translate', 'a short sentence', 'current')
    idle(qtbot, controller)
    assert controller._task_authorization is authorization
    assert [r['path'] for r in ollama_server.requests] == ['/api/chat']
    payload = ollama_server.requests[0]['payload']
    assert 'tools' not in payload and '[工具使用规则]' not in payload['messages'][0]['content']
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['origin'] == 'action' and not run['config']['allowed_tools']


def test_other_role_cannot_reexecute_old_tool_answer(qtbot, controller, tmp_path, ollama_server):
    grant(controller, 'code_expert', tmp_path)
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(answer('old role answer'))
    controller._on_user_message('original')
    idle(qtbot, controller)
    grant(controller, 'translator', tmp_path)
    count = len(ollama_server.requests)
    with patch.object(MessageBox, 'question') as question:
        controller._on_regenerate_requested()
    assert not question.called and len(ollama_server.requests) == count
    assert controller._worker is None and controller._pending_task is None


@pytest.mark.parametrize('admitted', ['false', 1, object()])
def test_truthy_admission_cannot_construct_executors(tmp_db, tmp_path, admitted):
    with patch('ai_desktop.llm.run_worker.SearchExecutor') as search:
        with pytest.raises(ValueError):
            RunWorker([], 'ROLE', agent_id='translator', tools_admitted=admitted,
                      search_settings=SearchSettings(), execution=ExecutionSnapshot.create(tmp_path))
    assert not search.called


@pytest.mark.parametrize('agent_id', ROLE_IDS)
@pytest.mark.parametrize('origin,admitted', [('chat', False), ('action', True)])
def test_ordinary_chat_and_actions_do_not_receive_tools_or_common_prompt(tmp_db, tmp_path, agent_id, origin, admitted):
    worker = RunWorker([], 'ROLE', agent_id=agent_id, origin=origin, tools_admitted=admitted,
                       execution=ExecutionSnapshot.create(tmp_path), search_settings=SearchSettings())
    assert not worker.context.tools and worker.search_executor is None
    assert worker.request.system_prompt == 'ROLE' and worker.context.limits.max_model_rounds == 1
    worker.release_attachments()
    worker.deleteLater()


def test_existing_disabled_setting_is_preserved(tmp_db, monkeypatch):
    storage.save_setting('general_assistant_tools_enabled', 'false')
    manager = SettingsManager()
    manager.load()
    assert config.CHAT_TOOLS_ENABLED is False
    manager.apply({'task_tools_enabled': True})
    assert config.CHAT_TOOLS_ENABLED is True


@pytest.mark.parametrize('agent_id', ['', ' ', 'bad\nrole'])
def test_invalid_agent_cannot_be_granted(tmp_path, agent_id):
    with pytest.raises(ValueError):
        TaskAuthorization(ExecutionSnapshot.create(tmp_path), agent_id=agent_id)
