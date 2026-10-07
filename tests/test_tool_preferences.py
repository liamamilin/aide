"""Saved tool choices survive scope changes, while execution remains run-scoped."""
import json
from unittest.mock import MagicMock, patch

import pytest

from ai_desktop import config
from ai_desktop.main import ChatController
from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot
from ai_desktop.services.task_admission import TaskAuthorization
from ai_desktop.services.tool_preferences import SETTING_KEY, ToolPreferences
from ai_desktop.services.web_search import SearchSettings
from ai_desktop.ui.task_dialog import TaskDialog
from ai_desktop.utils import storage
from tests.test_agent_tools import parameters as parameters
from tests.test_agent_tools import select
from tests.test_request_results import controller as controller
from tests.test_task_entry import answer, call_response, discovery, enqueue_discovery, idle


def save_choices(controller, workspace=None, provider='exa', policy=BashPolicy.READONLY_AUTO):
    grant = TaskAuthorization(ExecutionSnapshot.create(workspace, policy) if workspace else None,
                              SearchSettings(provider=provider) if provider else None,
                              agent_id=controller._active_agent.id)
    controller._save_tool_settings(grant)
    return grant


def test_missing_or_disabled_defaults_do_not_infer_old_permissions(tmp_db, tmp_path):
    storage.save_setting('execution_workspace', str(tmp_path))
    storage.save_setting('search_provider', 'exa')
    assert ToolPreferences.load() is None
    ToolPreferences.save(None)
    assert ToolPreferences.load() is None


def test_round_trip_keeps_workspace_identity_and_uses_current_search_limits(tmp_db, tmp_path, monkeypatch):
    original = ToolPreferences(ExecutionSnapshot.create(tmp_path, BashPolicy.CONFIRM_ALL), 'exa')
    ToolPreferences.save(original)
    restored = ToolPreferences.load()
    assert restored == original
    monkeypatch.setattr(config, 'SEARCH_MAX_RESULTS', 39)
    grant = restored.for_agent('translator')
    assert grant.agent_id == 'translator' and grant.execution == original.execution
    assert grant.search.provider == 'exa' and grant.search.max_results == 39
    record = json.loads(storage.get_setting(SETTING_KEY))
    assert set(record) == {'version', 'execution', 'search_provider'}
    assert 'agent_id' not in record and 'admission' not in record and 'approval' not in record


@pytest.mark.parametrize('raw', ['{broken', '[]', 'true', '{"version":2}',
                                '{"version":1,"execution":null,"search_provider":null}',
                                '{"version":1,"execution":null,"search_provider":"unknown"}',
                                '{"version":1,"execution":{},"search_provider":"exa"}'])
def test_invalid_saved_settings_leave_tools_off(tmp_db, raw):
    storage.save_setting(SETTING_KEY, raw)
    assert ToolPreferences.load() is None


def test_replaced_directory_does_not_gain_inherited_trust(tmp_db, tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    original = ToolPreferences(ExecutionSnapshot.create(workspace), None)
    ToolPreferences.save(original)
    workspace.rename(tmp_path/'old-workspace')
    workspace.mkdir()
    restored = ToolPreferences.load()
    assert restored.execution == original.execution
    assert not restored.for_agent('translator').execution.valid()


def test_new_conversations_keep_saved_choices_with_fresh_role_binding(controller, tmp_path):
    original = save_choices(controller, tmp_path)
    for _ in range(3):
        controller._new_conversation()
        grant = controller._task_authorization
        assert grant is not original and grant.agent_id == controller._active_agent.id
        assert grant.execution == original.execution and grant.search == original.search
        assert controller._dialog._task_btn.text() == '工具：已启用'
        assert controller._messages == [] and controller._pending_task is None
        original = grant


@pytest.mark.parametrize('via', ['selector', 'tray', 'visible', 'custom-edit'])
def test_agent_switch_rebinds_saved_choices(controller, tmp_path, via):
    original = save_choices(controller, tmp_path)
    other = next(a for a in controller._all_agents if a.id == 'translator')
    if via == 'selector':
        controller._dialog.set_active_agent(other)
        controller._on_agent_changed(other)
    elif via == 'tray':
        controller._on_tray_agent(other)
    elif via == 'visible':
        controller._dialog.set_active_agent(other)
        controller._sync_active_agent_from_dialog()
        controller._sync_action_context()
    else:
        select(controller, 'custom-researcher')
    grant = controller._task_authorization
    assert grant is not original and grant.agent_id == controller._active_agent.id
    assert grant.execution == original.execution and grant.search == original.search
    assert controller._dialog._task_btn.text() == '工具：已启用'


def test_history_uses_current_choices_not_historical_tool_configuration(controller, tmp_path):
    conversation = storage.create_conversation('translator', 'saved fixture')
    original = save_choices(controller, tmp_path, 'exa')
    controller._on_conversation_selected(conversation.id)
    assert controller._task_authorization.agent_id == 'translator'
    assert controller._task_authorization.execution == original.execution
    assert controller._task_authorization.search.provider == 'exa'
    assert controller._worker is None and controller._pending_task is None


def test_restart_loads_saved_choices_but_no_pending_run(qtbot, controller, tmp_path):
    original = save_choices(controller, tmp_path)
    with patch('ai_desktop.main.FloatButton'), patch('ai_desktop.main.MenuBarIcon'), \
            patch.object(ChatController, '_create_hotkey_backend', return_value=MagicMock()):
        restarted = ChatController()
    qtbot.addWidget(restarted._result_bubble)
    grant = restarted._task_authorization
    assert grant.agent_id == restarted._active_agent.id
    assert grant.execution == original.execution and grant.search == original.search
    assert restarted._worker is None and restarted._pending_task is None


def test_turning_off_from_panel_disables_future_scopes_and_saved_defaults(controller, tmp_path):
    save_choices(controller, tmp_path)
    def disable(panel):
        assert panel.authorization is not None
        panel._turn_off()
        return panel.result()
    with patch.object(TaskDialog, 'exec_', disable):
        controller._on_task_settings_requested()
    controller._new_conversation()
    select(controller, 'translator')
    assert controller._task_authorization is None and ToolPreferences.load() is None
    assert controller._dialog._task_btn.text() == '工具：关闭'


def test_settings_changes_keep_enabled_choices_and_apply_new_values(controller, tmp_path, monkeypatch):
    save_choices(controller, tmp_path)
    monkeypatch.setattr(config, 'SEARCH_PROVIDER', 'exa')
    monkeypatch.setattr(config, 'SEARCH_MAX_RESULTS', 5)
    controller._on_settings_applied({'search_provider': 'parallel', 'search_max_results': 47,
                                     'task_max_tool_calls': 39})
    assert controller._task_authorization.search.provider == 'parallel'
    assert controller._task_authorization.search.max_results == 47
    assert ToolPreferences.load().search_provider == 'parallel'
    controller._on_settings_applied({'task_tools_enabled': False})
    controller._new_conversation()
    assert controller._task_authorization is None
    controller._on_settings_applied({'task_tools_enabled': True})
    assert controller._task_authorization.search.max_results == 47


def test_inherited_role_runs_new_capability_check_and_keeps_role_prompt(qtbot, controller, tmp_path, ollama_server):
    (tmp_path/'note.txt').write_text('INHERITED-237')
    save_choices(controller, tmp_path, None)
    for agent_id in ['translator', 'summarizer']:
        controller._new_conversation()
        agent = select(controller, agent_id)
        enqueue_discovery(ollama_server, discovery(controller._model))
        ollama_server.enqueue(call_response('cat note.txt'))
        ollama_server.enqueue(answer('INHERITED-237'))
        controller._on_user_message('read note.txt')
        idle(qtbot, controller)
        payload = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat'][-1]
        assert payload['messages'][0]['content'].startswith(agent.system_prompt)
        assert payload['tools'][0]['function']['name'] == 'bash'
    assert len([r for r in ollama_server.requests if r['path'] == '/api/show']) == 2


@pytest.mark.parametrize('switch', ['new', 'agent', 'history'])
def test_saved_choices_do_not_inherit_pending_command_approval(qtbot, controller, tmp_path,
                                                               ollama_server, switch):
    save_choices(controller, tmp_path, None, BashPolicy.CONFIRM_ALL)
    controller._dialog.show()
    controller._dialog.set_auto_hide(False)
    enqueue_discovery(ollama_server, discovery(controller._model))
    ollama_server.enqueue(call_response('printf stale > stale.txt'))
    controller._on_user_message('write fixture')
    qtbot.waitUntil(lambda: any(c.pending for c in controller._dialog._tool_cards.values()), timeout=2500)
    card = next(c for c in controller._dialog._tool_cards.values() if c.pending)
    confirmation = card.confirmation
    if switch == 'new':
        controller._new_conversation()
    elif switch == 'agent':
        select(controller, 'translator')
    else:
        other = storage.create_conversation('translator', 'history fixture')
        controller._on_conversation_selected(other.id)
    controller._on_command_decided(confirmation, True)
    idle(qtbot, controller)
    assert controller._task_authorization is not None and not card.pending
    assert not (tmp_path/'stale.txt').exists()


def test_inherited_settings_do_not_enable_tools_for_quick_actions(qtbot, controller, tmp_path, ollama_server):
    save_choices(controller, tmp_path)
    controller._new_conversation()
    ollama_server.enqueue(answer('translation'))
    controller._on_action_requested('translate', 'hello', 'new')
    idle(qtbot, controller)
    payload = next(r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat')
    assert 'tools' not in payload
    assert ToolPreferences.load() is not None


@pytest.mark.parametrize('switch', ['new', 'agent'])
def test_inherited_settings_do_not_reuse_late_model_discovery(qtbot, controller, tmp_path,
                                                             ollama_server, switch):
    save_choices(controller, tmp_path, None)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message('pending fixture')
    qtbot.waitUntil(scenario.received.is_set)
    pending = controller._pending_task.copy()
    if switch == 'new':
        controller._new_conversation()
    else:
        select(controller, 'translator')
    controller._on_task_checked(pending['sequence'], None, '')
    idle(qtbot, controller)
    assert controller._task_authorization is not None
    assert controller._task_authorization is not pending['authorization']
    assert not storage.list_conversations()
    assert all(r['path'] != '/api/chat' for r in ollama_server.requests)


def test_workspace_settings_change_updates_future_choices(controller, tmp_path):
    first = tmp_path/'first'
    second = tmp_path/'second'
    first.mkdir()
    second.mkdir()
    save_choices(controller, first, None)
    controller._on_settings_applied({'execution_workspace': str(second),
                                     'bash_policy': BashPolicy.CONFIRM_ALL.value})
    controller._new_conversation()
    grant = controller._task_authorization
    assert grant.execution.workspace == str(second) and grant.execution.policy == BashPolicy.CONFIRM_ALL
    assert ToolPreferences.load().execution == grant.execution
