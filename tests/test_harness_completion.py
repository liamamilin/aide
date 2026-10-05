"""H6: action modes/retries stay isolated on the sole real Qt run worker."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.llm.service_checks import ImageCapability
from ai_desktop.llm.thinking import ThinkingCapability, cache_thinking
from ai_desktop.services import audit_store
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.services.task_admission import TaskAuthorization
from ai_desktop.services.web_search import SearchSettings
from ai_desktop.utils import storage
from tests import test_request_results as requests

controller = requests.controller


def wait_idle(qtbot, ctl):
    qtbot.waitUntil(lambda: ctl._worker is None and not ctl._stale_workers, timeout=4000)


def general_conversation(ctl):
    convo = storage.create_conversation('general_assistant', 'existing')
    storage.save_message(convo.id, 'user', 'previous question')
    storage.save_message(convo.id, 'assistant', 'previous answer')
    ctl._on_conversation_selected(convo.id)
    return convo.id


def configure_action(ctl, action_id, customized=False):
    cache_thinking(config.OLLAMA_BASE_URL, 'action-model', '', ThinkingCapability((False, True), True, True))
    ctl._profile_mgr.save(ModelProfile('action-fast', '动作模型', 'action-model', False, .15, 222))
    action = ctl._action_service.get(action_id)
    if customized:
        action = replace(action, agent_id='general_assistant', instruction='CUSTOM: explain the material briefly')
    action = ctl._action_service.save(replace(action, profile_id='action-fast'))
    return action


@pytest.mark.parametrize('action_id,customized', [
    ('translate', False), ('explain', False), ('summarize', False), ('rewrite', False), ('translate', True),
])
@pytest.mark.parametrize('mode', ['new', 'current'])
@pytest.mark.parametrize('retry_finish', ['stop', 'length'])
def test_action_matrix_preserves_plan_profile_single_round_and_answer_versions(
    qtbot, controller, ollama_server, tmp_path, monkeypatch, action_id, customized, mode, retry_finish,
):
    old_convo = general_conversation(controller)
    action = configure_action(controller, action_id, customized)
    controller._task_authorization = TaskAuthorization(
        ExecutionSnapshot.create(tmp_path), SearchSettings(provider='exa'))
    plan = controller._action_service.build_request_plan(action_id, 'selected material', controller._all_agents)
    ollama_server.enqueue({'message': {'content': 'first answer'}, 'done': True, 'done_reason': 'stop'})
    controller._on_action_requested(action_id, 'selected material', mode)
    first = controller._worker
    assert type(first) is RunWorker
    assert not first.context.tools and first.context.limits.max_model_rounds == 1
    first_run = first.request.run_id
    wait_idle(qtbot, controller)
    convo_id = controller._convo_id
    assert (convo_id == old_convo) == (mode == 'current')
    assert controller._active_agent.id == ('general_assistant' if mode == 'current' else action.agent_id)
    assert (controller._task_authorization is not None) == (mode == 'current')
    user = next(msg for msg in reversed(controller._messages) if msg.role == 'user')
    # Change visible chat settings: an Action retry must still resolve its Action profile.
    controller._model = 'text-model'
    monkeypatch.setattr(config, 'OLLAMA_NUM_PREDICT', 77)
    ollama_server.enqueue({'message': {'content': 'retry answer'}, 'done': True, 'done_reason': retry_finish})
    controller._on_regenerate_requested()
    retry = controller._worker
    assert type(retry) is RunWorker and retry.request.run_id != first_run
    assert retry.request.agent_id == plan.agent.id
    assert not retry.context.tools and retry.context.execution is None and retry.context.search_settings is None
    assert retry.context.limits.max_model_rounds == 1
    wait_idle(qtbot, controller)
    assert len(ollama_server.requests) == 2
    for request in ollama_server.requests:
        payload = request['payload']
        assert payload['model'] == 'action-model' and payload['think'] is False
        assert payload['options']['num_predict'] == 222 and payload['options']['temperature'] == .15
        assert not payload.get('tools')
        assert payload['messages'][0] == {'role': 'system', 'content': plan.system_prompt}
        assert payload['messages'][-1] == {'role': 'user', 'content': 'selected material'}
        assert all(msg['role'] != 'tool' for msg in payload['messages'])
        assert [msg['content'] for msg in payload['messages'][1:-1]] == (
            ['previous question', 'previous answer'] if mode == 'current' else [])
    versions = storage.list_generations(user.id)
    assert len(versions) == 2
    assert len([msg for msg in storage.get_conversation(convo_id).messages
                if msg.role == 'user' and msg.content == 'selected material']) == 1
    active = storage.get_active_generation(user.id)
    assert active.answer == ('retry answer' if retry_finish == 'stop' else 'first answer')
    last = versions[-1]
    assert last.config_snapshot['origin'] == 'action' and last.config_snapshot['action_id'] == action_id
    assert last.config_snapshot['agent_id'] == plan.agent.id and not last.config_snapshot['allowed_tools']
    assert last.config_snapshot.get('run_status', 'succeeded') == ('succeeded' if retry_finish == 'stop' else 'limited')
    runs = audit_store.list_runs(convo_id)
    assert len(runs) == 2
    assert {run['run_id'] for run in runs} == {first_run, retry.request.run_id}
    assert all(run['origin'] == 'action' and len(run['steps']) == 1 for run in runs)
    requests.assert_input_ready(controller)


@pytest.mark.parametrize('broken', ['hidden', 'missing_id'])
def test_action_retry_unavailable_never_becomes_plain_chat(qtbot, controller, ollama_server, broken):
    general_conversation(controller)
    configure_action(controller, 'translate')
    ollama_server.enqueue({'message': {'content': 'first answer'}, 'done': True})
    controller._on_action_requested('translate', 'material', 'current')
    wait_idle(qtbot, controller)
    user = next(msg for msg in reversed(controller._messages) if msg.role == 'user')
    if broken == 'hidden':
        action = controller._action_service.get('translate')
        controller._action_service.save(replace(action, enabled=False))
    else:
        with storage._conn() as conn:
            row = storage.get_active_generation(user.id)
            snapshot = dict(row.config_snapshot)
            snapshot.pop('action_id')
            conn.execute('UPDATE generations SET config_snapshot=? WHERE id=?',
                         (json.dumps(snapshot), row.id))
    with patch.object(controller, '_show_notice') as notice:
        controller._on_regenerate_requested()
    assert controller._worker is None and len(ollama_server.requests) == 1
    assert len(storage.list_generations(user.id)) == 1
    assert notice.call_args.args[1] == '无法重新执行快捷动作'


def test_retry_uses_updated_action_definition_explicitly(qtbot, controller, ollama_server):
    general_conversation(controller)
    action = configure_action(controller, 'translate')
    ollama_server.enqueue({'message': {'content': 'first'}, 'done': True})
    controller._on_action_requested('translate', 'material', 'current')
    wait_idle(qtbot, controller)
    controller._action_service.save(replace(action, instruction='NEW ACTION INSTRUCTION'))
    ollama_server.enqueue({'message': {'content': 'retry'}, 'done': True})
    controller._on_regenerate_requested()
    wait_idle(qtbot, controller)
    assert 'NEW ACTION INSTRUCTION' in ollama_server.requests[-1]['payload']['messages'][0]['content']
    assert 'NEW ACTION INSTRUCTION' not in ollama_server.requests[0]['payload']['messages'][0]['content']


@pytest.mark.parametrize('supported', [True, False])
def test_regeneration_checks_resolved_model_for_images(controller, tmp_path, monkeypatch, supported):
    from PyQt5.QtGui import QImage
    image = tmp_path/'attachment.png'
    QImage(4, 4, QImage.Format_RGB32).save(str(image))
    convo_id = general_conversation(controller)
    user = storage.save_message(convo_id, 'user', 'image question', images=[str(image)])
    controller._messages.append(user)
    controller._profile_mgr.save(ModelProfile('image-profile', '图片模型', 'profile-model', False))
    controller._active_agent = replace(controller._active_agent, profile_id='image-profile')
    controller._model = 'text-model'
    monkeypatch.setattr(controller, '_image_capability_for_model', lambda model:
                        ImageCapability.SUPPORTED if (model == 'profile-model') == supported
                        else ImageCapability.UNSUPPORTED)
    with patch.object(RunWorker, 'start'), patch.object(controller, '_show_notice') as notice:
        controller._start_regeneration(user)
    worker = controller._worker
    if supported:
        assert worker is not None and worker.request.model == 'profile-model'
        controller._worker = None
        worker.release_attachments()
        worker.deleteLater()
    else:
        assert worker is None and 'profile-model' in notice.call_args.args[2]
