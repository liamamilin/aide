"""Tool previews, discovery, HTTP requests and retries share effective settings."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.model_options import global_options
from ai_desktop.llm.task_checks import TaskChecks
from ai_desktop.llm.thinking import ThinkMode, ThinkSetting
from ai_desktop.services import audit_store
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.services.task_admission import TaskModelSettings, validate_discovery
from ai_desktop.ui.fluent import MessageBox
from ai_desktop.ui.task_dialog import TaskDialog, probe_task_entry
from ai_desktop.utils import storage
from tests.test_request_results import controller as controller
from tests.test_task_entry import answer, authorize, call_response, discovery, enqueue_discovery, idle


@pytest.fixture(autouse=True)
def generation_settings(monkeypatch):
    for key, value in {'NUM_CTX': 81920, 'NUM_PREDICT': 20477, 'TEMPERATURE': 0.35,
                       'TOP_P': 0.85, 'TOP_K': 40, 'REPEAT_PENALTY': 1.1, 'THINK': False}.items():
        monkeypatch.setattr(config, 'OLLAMA_' + key, value)


def test_tool_preview_matches_wire_and_audit_in_every_step(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    model = 'qwen3.8:27b-mlx'
    controller._on_model_changed(model)
    settings = TaskModelSettings.from_config(controller._resolve_model_config())
    dialog = TaskDialog(model=model, settings=settings)
    qtbot.addWidget(dialog)
    assert '上下文 81920' in dialog._profile_summary.text()
    assert '输出 20477' in dialog._profile_summary.text()
    assert '思考：关闭（全局设置）' in dialog._profile_summary.text()
    (tmp_path/'note.txt').write_text('AMBER-731')
    enqueue_discovery(ollama_server, discovery(model))
    ollama_server.enqueue(call_response('cat note.txt'))
    ollama_server.enqueue(answer())
    controller._on_user_message('read note')
    idle(qtbot, controller)
    payloads = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(payloads) == 2
    assert all(p['model'] == model and p['options'] == dict(settings.options) and p['think'] is False
               for p in payloads)
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['status'] == 'succeeded'
    assert run['config']['options'] == run['config']['admission']['options'] == global_options()
    assert run['config']['think_source'] == '全局设置'
    assert not run['config']['admission']['budget_verified']


def test_role_overrides_follow_topbar_model_with_fresh_thinking(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    model = 'qwen3.8:27b-mlx'
    controller._on_model_changed(model)
    setting = ThinkSetting(ThinkMode.NAMED, 'xhigh')
    controller._profile_mgr.save(ModelProfile('role', '英文专用', 'profile-model', setting, 0.7, 3000))
    controller._active_agent = replace(controller._active_agent, profile_id='role')
    controller._dialog.set_active_agent(controller._active_agent)
    settings = TaskModelSettings.from_config(controller._resolve_model_config())
    dialog = TaskDialog(model=model, settings=settings)
    qtbot.addWidget(dialog)
    assert '输出 3000' in dialog._profile_summary.text() and '英文专用' in dialog._profile_summary.text()
    metadata = list(discovery(model))
    metadata[2]['thinking'] = {'values': [False, 'low', 'xhigh'], 'default': 'low'}
    enqueue_discovery(ollama_server, metadata)
    ollama_server.enqueue(answer('profile answer'))
    with patch.object(controller, '_show_notice') as notice:
        controller._on_user_message('question')
        idle(qtbot, controller)
    payload = ollama_server.requests[-1]['payload']
    assert payload['model'] == model and payload['think'] == 'xhigh'
    assert payload['options'] == {**global_options(), 'num_predict': 3000, 'temperature': 0.7}
    assert not notice.called  # Cached capability of the other profile model must not cause a false warning.
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run['config']['think_source'] == '英文专用'
    assert run['config']['think_setting'] == setting.record()


@pytest.mark.parametrize('setting,metadata,wire,warns', [
    (ThinkSetting(ThinkMode.OFF), {'values': [False, True]}, False, False),
    (ThinkSetting(ThinkMode.ON), {'values': [False, True]}, True, False),
    (ThinkSetting(), {'values': [False, True]}, None, False),
    (ThinkSetting(ThinkMode.NAMED, 'medium'), {'values': [False, 'medium']}, 'medium', False),
    (ThinkSetting(ThinkMode.ON), {'values': [False, 'medium']}, None, True),
    (ThinkSetting(ThinkMode.OFF), None, None, True),
])
def test_requested_thinking_is_resolved_against_selected_model(monkeypatch, setting, metadata, wire, warns):
    monkeypatch.setattr(config, 'OLLAMA_THINK', setting)
    values = list(discovery())
    if metadata is None:
        values[2].pop('thinking')
    else:
        values[2]['thinking'] = metadata
    admission = validate_discovery('http://localhost:11434', *values)
    assert admission.think == wire and type(admission.think) is type(wire)
    assert bool(admission.warnings) == warns
    assert admission.record()['requested_think'] == setting.record()


@pytest.mark.parametrize('change', ['declared_context', 'output_exceeds_context'])
def test_invalid_context_budget_preserves_draft_without_model_request(
        qtbot, controller, tmp_path, ollama_server, monkeypatch, change):
    authorize(controller, tmp_path)
    values = list(discovery())
    if change == 'declared_context':
        values[2]['model_info'] = {'architecture.context_length': 65536}
    else:
        monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 90000)
    enqueue_discovery(ollama_server, values)
    with patch.object(controller, '_show_notice') as notice:
        controller._on_user_message('preserve draft')
        idle(qtbot, controller)
    assert '81920' in notice.call_args.args[2]
    assert controller._dialog._input.toPlainText() == 'preserve draft'
    assert not storage.list_conversations()
    assert not any(r['path'] == '/api/chat' for r in ollama_server.requests)


def test_reexecution_and_followup_use_updated_parameters_keep_grant(qtbot, controller, tmp_path, ollama_server):
    authorize(controller, tmp_path)
    grant = controller._task_authorization
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer('first'))
    controller._on_user_message('question')
    idle(qtbot, controller)
    controller._on_settings_applied({'num_ctx': 65536, 'num_predict': 4000, 'temperature': 0.6})
    assert controller._task_authorization is grant
    expected = global_options()
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer('retry'))
    with patch.object(MessageBox, 'question', return_value=MessageBox.Yes):
        controller._on_regenerate_requested()
    idle(qtbot, controller)
    assert ollama_server.requests[-1]['payload']['options'] == expected
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer('followup'))
    controller._on_user_message('followup question')
    idle(qtbot, controller)
    assert ollama_server.requests[-1]['payload']['options'] == expected
    assert controller._task_authorization is grant
    user = next(m for m in controller._messages if m.role == 'user')
    assert len(storage.list_generations(user.id)) == 2


@pytest.mark.parametrize('change', ['settings', 'profile', 'assignment', 'direct_mutation'])
def test_changed_parameters_cancel_stale_check_restore_draft(
        qtbot, controller, tmp_path, ollama_server, monkeypatch, change):
    authorize(controller, tmp_path)
    grant = controller._task_authorization
    scenario = ollama_server.enqueue(before_headers=True)
    controller._on_user_message('keep pending draft')
    qtbot.waitUntil(scenario.received.is_set)
    pending = controller._pending_task
    if change == 'settings':
        controller._on_settings_applied({'num_ctx': 65536})
    elif change == 'profile':
        controller._on_profiles_saved([ModelProfile('new-profile', 'New', num_predict=5000)])
    elif change == 'assignment':
        controller._profile_mgr.save(ModelProfile('new-profile', 'New', num_predict=5000))
        controller._on_agent_profile_changed('general_assistant', 'new-profile')
    else:
        monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 65536)
        old = validate_discovery(ollama_server.url, *discovery(), settings=pending['model_settings'])
        controller._on_task_checked(pending['sequence'], old, '')
    idle(qtbot, controller)
    assert controller._dialog._input.toPlainText() == 'keep pending draft'
    assert controller._task_authorization is grant and not storage.list_conversations()
    assert not any(r['path'] == '/api/chat' for r in ollama_server.requests)


def test_discovery_keeps_original_parameter_snapshot(qtbot, ollama_server, monkeypatch):
    checks = TaskChecks()
    values = discovery()
    first = ollama_server.enqueue(values[0], delay=0.15)
    for value in values[1:]:
        ollama_server.enqueue(value)
    settings = TaskModelSettings.from_config()
    with qtbot.waitSignal(checks.completed, timeout=2000) as signal:
        checks.check(ollama_server.url, settings=settings)
        qtbot.waitUntil(first.received.is_set)
        monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 65536)
    assert not signal.args[2] and signal.args[1].settings == settings
    assert dict(signal.args[1].settings.options)['num_ctx'] == 81920


def test_packaged_ui_probe_uses_its_own_small_fixture(controller, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_NUM_CTX', 8192)
    monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 20480)
    before = global_options()
    probe_task_entry(controller._dialog)
    assert global_options() == before
    assert controller._worker is None and controller._task_authorization is None
