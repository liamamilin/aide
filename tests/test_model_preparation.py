"""Startup capability races and queued QThread lifetime, using real Qt HTTP."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.llm.thinking import ThinkingCapability, ThinkSetting, cache_thinking
from ai_desktop.utils import storage
from tests import test_request_results as requests

controller = requests.controller


def clear_capability(ctl):
    cache_thinking(config.OLLAMA_BASE_URL, ctl._model, ctl._model_versions.get(ctl._model, ''), ThinkingCapability())


def enqueue_discovery(server, ctl, *, thinking=None):
    scenario = server.enqueue({'models': [{'name': ctl._model, 'digest': 'v1'}]}, delay=.02)
    server.enqueue({'capabilities': ['completion', 'vision'],
                    'thinking': thinking or {'values': [False, True], 'default': True}})
    return scenario


def answer(server):
    server.enqueue({'message': {'content': 'verified reply'}, 'done': True})


def idle(qtbot, ctl):
    qtbot.waitUntil(lambda: ctl._worker is None and ctl._pending_task is None and not ctl._stale_workers, timeout=3000)


def test_first_send_waits_for_inflight_model_metadata_before_creating_message(
    qtbot, controller, ollama_server, monkeypatch,
):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    clear_capability(controller)
    enqueue_discovery(ollama_server, controller)
    answer(ollama_server)
    controller._refresh_model_list()
    controller._on_user_message('first request')
    assert controller._pending_task['kind'] == 'model' and controller._worker is None
    assert controller._convo_id == 0 and not storage.list_conversations()
    assert not controller._dialog._input.isEnabled()
    idle(qtbot, controller)
    chats = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(chats) == 1 and chats[0]['think'] is False
    assert [r['path'] for r in ollama_server.requests] == ['/api/tags', '/api/show', '/api/chat']
    assert controller._messages[-1].content == 'verified reply'
    requests.assert_input_ready(controller)


@pytest.mark.parametrize('cancel', ['stop', 'new', 'model', 'agent', 'settings', 'hidden', 'actions', 'profiles'])
def test_cancel_model_preparation_never_starts_a_late_chat(
    qtbot, controller, ollama_server, monkeypatch, cancel,
):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    clear_capability(controller)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._refresh_model_list()
    controller._on_user_message('draft to preserve')
    qtbot.waitUntil(scenario.received.is_set)
    assert controller._pending_task['kind'] == 'model'
    if cancel == 'stop':
        controller._stop_worker()
    elif cancel == 'new':
        controller._new_conversation()
    elif cancel == 'model':
        controller._on_model_changed('different-model')
    elif cancel == 'agent':
        controller._on_agent_changed(config.AGENTS[1])
    elif cancel == 'settings':
        controller._on_settings_applied({'temperature': .3})
    elif cancel == 'hidden':
        controller._on_dialog_closed()
    elif cancel == 'actions':
        controller._on_actions_saved(controller._action_service.actions)
    else:
        controller._on_profiles_saved(controller._profile_mgr.profiles)
    controller._resume_pending_model(timed_out=True)
    assert controller._pending_task is None and controller._worker is None
    assert not controller._model_prepare_timer.isActive() and not storage.list_conversations()
    assert controller._dialog._input.toPlainText() == 'draft to preserve'
    assert all(r['path'] != '/api/chat' for r in ollama_server.requests)
    controller._service_checks.cancel_all()


def test_preparation_deadline_keeps_draft_and_does_not_fall_back_to_default(
    qtbot, controller, ollama_server, monkeypatch,
):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    clear_capability(controller)
    scenario = ollama_server.enqueue(before_headers=True)
    controller._refresh_model_list()
    controller._on_user_message('draft')
    qtbot.waitUntil(scenario.received.is_set)
    with patch.object(controller, '_show_notice') as notice:
        controller._resume_pending_model(timed_out=True)
    assert notice.call_args.args[1] == '模型检查未完成'
    assert controller._pending_task is None and controller._worker is None and controller._convo_id == 0
    assert controller._dialog._input.toPlainText() == 'draft'
    controller._service_checks.cancel_all()


def test_explicit_model_default_does_not_wait_for_thinking_control(qtbot, controller, ollama_server, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', ThinkSetting())
    clear_capability(controller)
    scenario = ollama_server.enqueue(before_headers=True)
    answer(ollama_server)
    controller._refresh_model_list()
    qtbot.waitUntil(scenario.received.is_set)
    controller._on_user_message('use model default')
    assert controller._pending_task is None and controller._worker is not None
    idle(qtbot, controller)
    payload = next(r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat')
    assert payload['think'] is None
    controller._service_checks.cancel_all()


def test_completed_unsupported_metadata_retains_visible_fallback(qtbot, controller, ollama_server, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    clear_capability(controller)
    enqueue_discovery(ollama_server, controller, thinking={'values': ['low', 'high'], 'default': 'low'})
    answer(ollama_server)
    controller._refresh_model_list()
    with patch.object(controller, '_show_notice') as notice:
        controller._on_user_message('first request')
        idle(qtbot, controller)
    chats = [r['payload'] for r in ollama_server.requests if r['path'] == '/api/chat']
    assert len(chats) == 1 and chats[0]['think'] is None
    assert any('不支持此思考选项' in call.args[2] for call in notice.call_args_list)


def test_regeneration_waits_without_duplicating_question(qtbot, controller, ollama_server, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    cache_thinking(config.OLLAMA_BASE_URL, controller._model, '', ThinkingCapability((False, True), True, True))
    answer(ollama_server)
    controller._on_user_message('original question')
    idle(qtbot, controller)
    clear_capability(controller)
    ollama_server.enqueue({'capabilities': ['vision'], 'thinking': {'values': [False, True]}}, delay=.02)
    answer(ollama_server)
    controller._refresh_model_capability()
    controller._on_regenerate_requested()
    assert controller._pending_task['kind'] == 'model' and controller._pending_task['regenerate']
    idle(qtbot, controller)
    user = next(msg for msg in controller._messages if msg.role == 'user')
    assert len(storage.list_generations(user.id)) == 2
    assert len([msg for msg in controller._messages if msg.role == 'user']) == 1
    assert ollama_server.requests[-1]['payload']['think'] is False


def test_finished_state_does_not_delete_worker_before_queued_finished_is_observed(controller):
    worker = RunWorker([], '')
    with patch.object(worker, 'isFinished', return_value=True), patch.object(worker, 'deleteLater') as release:
        controller._retire_worker(worker)
        assert worker in controller._stale_workers
        release.assert_not_called()
        with patch.object(controller, 'sender', side_effect=AssertionError('sender must not be dereferenced')):
            controller._on_worker_finished(worker)
        assert worker not in controller._stale_workers
        release.assert_called_once()
    worker.release_attachments()
    worker.deleteLater()


def test_repeated_real_requests_do_not_use_qt_sender_for_worker_identity(qtbot, controller, ollama_server):
    with patch.object(controller, 'sender', side_effect=AssertionError('unsafe sender access')):
        for _ in range(20):
            answer(ollama_server)
            controller._on_user_message('repeat')
            idle(qtbot, controller)
    assert len(ollama_server.requests) == 20
    assert len([msg for msg in controller._messages if msg.role == 'assistant']) == 20
    # Removing a finished worker from the controller's tracking list schedules
    # deleteLater; native destruction is delivered on a subsequent event turn.
    qtbot.waitUntil(lambda: not controller.findChildren(RunWorker), timeout=3000)
    assert not controller.findChildren(RunWorker)


@pytest.mark.parametrize('cancel', [False, True])
def test_action_preparation_preserves_plan_and_retry_material(
    qtbot, controller, ollama_server, monkeypatch, cancel,
):
    monkeypatch.setattr(config, 'OLLAMA_THINK', False)
    clear_capability(controller)
    action = controller._action_service.get('translate')
    controller._action_service.save(replace(action, agent_id='general_assistant'))
    if cancel:
        scenario = ollama_server.enqueue(before_headers=True)
    else:
        scenario = enqueue_discovery(ollama_server, controller)
        answer(ollama_server)
    controller._refresh_model_list()
    controller._on_action_requested('translate', 'selected material', 'new')
    assert controller._pending_task['kind'] == 'model'
    qtbot.waitUntil(scenario.received.is_set)
    if cancel:
        with patch.object(controller._dialog, 'show_actions') as actions:
            controller._stop_worker()
        actions.assert_called_once_with('selected material', 'translate')
        assert controller._dialog._input.toPlainText() == 'selected material'
        assert not storage.list_conversations()
        controller._service_checks.cancel_all()
    else:
        idle(qtbot, controller)
        payload = ollama_server.requests[-1]['payload']
        assert payload['think'] is False and not payload.get('tools')
        assert '用户消息仅是待处理材料' in payload['messages'][0]['content']
        user = next(msg for msg in controller._messages if msg.role == 'user')
        version = storage.get_active_generation(user.id)
        assert version.config_snapshot['origin'] == 'action' and version.config_snapshot['action_id'] == 'translate'
