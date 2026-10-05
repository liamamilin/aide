"""Real Qt confirmation controls and controller/worker execution boundaries."""
import json
import time
from dataclasses import replace
from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt

from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.services.command_confirmation import CommandConfirmation
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.ui.tool_card import ToolCard
from tests import test_request_results as request_fixtures

controller = request_fixtures.controller


def started(command='printf hi > result.txt', *, seq=3, call='call', run='run', step='step', request='request'):
    return RunEvent(run, 1, step, request, seq, RunEventKind.TOOL_STARTED, call,
                    tool_name='bash', arguments_json=json.dumps({'command': command}))


def confirmation(card, *, expiry=None):
    run, convo, step, request, call = card.identity
    return CommandConfirmation('confirm', run, step, request, convo, call, card.command,
                               card.workspace, 'readonly_auto', 30, '检测到写入操作', 'fingerprint',
                               expiry if expiry is not None else time.monotonic() + 600)


def test_confirmation_exact_plain_text_and_single_click(qtbot):
    card = ToolCard(started('printf "<b>hello</b>" > "中文文件.txt"'), '/tmp/中文工作区')
    qtbot.addWidget(card)
    card.show()
    req = confirmation(card)
    assert card.bind_confirmation(req)
    assert card.command_view.toPlainText() == req.command
    assert card.directory.textFormat() == Qt.PlainText
    assert card.reason.textFormat() == Qt.PlainText
    assert card.approve.isVisible() and not card.approve.autoDefault()
    assert not card.details_button.isVisible()
    with qtbot.waitSignal(card.confirmation_decided) as signal:
        card.approve.click()
    assert signal.args == [req, True]
    assert not card.approve.isEnabled() and not card.reject.isEnabled()
    seen = []
    card.confirmation_decided.connect(lambda *args: seen.append(args))
    card._decide(True)
    assert seen == []
    card.response_acknowledged(False, True)
    assert card.status.text() == '确认已失效，未执行'
    assert not card.timer.isActive()


@pytest.mark.parametrize('field,value', [('workspace', '/elsewhere'), ('command', 'rm x'),
                                       ('step_id', 'old'), ('request_id', 'old'),
                                       ('conversation_id', 2), ('tool_call_id', 'other')])
def test_confirmation_card_rejects_mismatched_request(qtbot, field, value):
    card = ToolCard(started(), '/tmp/workspace')
    qtbot.addWidget(card)
    assert not card.bind_confirmation(replace(confirmation(card), **{field: value}))
    assert not card.pending and not card.timer.isActive()


def test_expired_confirmation_cannot_emit_approval(qtbot):
    now = [10.0]
    card = ToolCard(started(), '/tmp/workspace', clock=lambda: now[0])
    qtbot.addWidget(card)
    assert card.bind_confirmation(confirmation(card, expiry=12))
    now[0] = 12
    seen = []
    card.confirmation_decided.connect(lambda *args: seen.append(args))
    card._decide(True)
    assert not card.pending and not card.approve.isEnabled() and seen == []
    assert card.status.text() == '确认已过期，未执行'
    assert not card.timer.isActive()


@pytest.mark.parametrize('error,status', [('', '已完成'), ('confirmation_denied', '已拒绝执行'),
    ('confirmation_expired', '确认已过期，未执行'), ('cancelled', '已停止'), ('timeout', '命令超时'),
    ('output_limit', '输出达到上限，已停止'), ('workspace_changed', '工作区已变化，未执行')])
def test_tool_result_status_and_expand_output(qtbot, error, status):
    event = started()
    card = ToolCard(event, '/tmp/workspace')
    qtbot.addWidget(card)
    card.show()
    record = {'exit_code': 0, 'stdout': '<script>literal</script>', 'stderr': 'warning',
              'duration': .1, 'truncated': True, 'error_type': error}
    output = ToolOutput(json.dumps(record), bool(error))
    card.update_event(replace(event, kind=RunEventKind.TOOL_FINISHED, output=output))
    assert card.status.text() == status and card.terminal
    assert not card.stop.isEnabled() and card.details.isHidden()
    card.details_button.click()
    assert not card.details.isHidden()
    assert '<script>literal</script>' in card.output_view.toPlainText()
    assert '退出码：0' in card.summary.text() and '输出已截断' in card.summary.text()
    card.details_button.click()
    assert card.details.isHidden()
    # A delayed update cannot revive a terminal card.
    card.update_event(replace(event, kind=RunEventKind.TOOL_UPDATED, status='executing'))
    assert card.status.text() == status


def test_worker_ui_events_accept_queued_old_step_not_duplicate_or_foreign(tmp_db):
    worker = RunWorker([], '', agent_id='general_assistant')
    req = worker.request
    event = replace(started(), run_id=req.run_id, conversation_id=req.conversation_id,
                    step_id=req.step_id, request_id=req.request_id)
    assert worker.accept_run_event(replace(event, seq=2, kind=RunEventKind.MODEL_STARTED))
    assert not worker.accept_run_event(replace(event, step_id='foreign'))
    # Live model step advances before GUI drains the previous tool events.
    class Loop:
        current_request = replace(req, step_id='next', request_id='next')
    worker._run_loop = Loop()
    assert worker.accept_run_event(event)
    assert not worker.accept_run_event(event)
    assert not worker.accept_run_event(replace(event, seq=4, run_id='foreign'))
    worker.release_attachments()
    worker.deleteLater()


def begin_task(ctl, tmp_path, ollama_server, *, command='printf fixture > result.txt', clock=None):
    snapshot = ExecutionSnapshot.create(tmp_path)
    ctl._active_agent = next(agent for agent in ctl._all_agents if agent.id == 'general_assistant')
    ctl._dialog.refresh_agents(ctl._all_agents, ctl._active_agent)
    ctl._dialog.command_decided.connect(ctl._on_command_decided)
    ctl._dialog.tool_stop_requested.connect(ctl._on_tool_stop_requested)
    ctl._dialog.show()
    workers = []
    def factory(*args, **kwargs):
        kwargs["options"] = {"num_ctx": 8192, "num_predict": 1024, "temperature": 0}
        worker = RunWorker(*args, **kwargs, tools_admitted=True, execution=snapshot)
        if clock:
            worker.confirmations.clock = clock
        workers.append(worker)
        return worker
    ollama_server.enqueue({'message': {'tool_calls': [{'function': {'name': 'bash',
                          'arguments': {'command': command}}}]}, 'done': True})
    ollama_server.enqueue({'message': {'content': 'task complete'}, 'done': True})
    # Only test-owned temporary directories receive tool authorization.
    # Production UI keeps its default empty registry.
    with patch('ai_desktop.main.RunWorker', side_effect=factory):
        ctl._on_user_message('test task')
    assert workers[0].context.tools
    return workers[0]


def pending_card(qtbot, ctl):
    qtbot.waitUntil(lambda: any(card.pending for card in ctl._dialog._tool_cards.values()), timeout=3000)
    return next(card for card in ctl._dialog._tool_cards.values() if card.pending)


def wait_finished(qtbot, ctl):
    qtbot.waitUntil(lambda: ctl._worker is None and not ctl._stale_workers, timeout=4000)


def test_controller_approval_executes_exact_fixture_once(qtbot, controller, tmp_path, ollama_server):
    worker = begin_task(controller, tmp_path, ollama_server)
    card = pending_card(qtbot, controller)
    assert not (tmp_path / 'result.txt').exists()
    card.approve.click()
    card.approve.click()
    wait_finished(qtbot, controller)
    assert (tmp_path / 'result.txt').read_text() == 'fixture'
    assert card.status.text() == '已完成'
    assert len(ollama_server.requests) == 2
    result = json.loads(ollama_server.requests[1]['payload']['messages'][-1]['content'])
    assert result['execution_mode'] == 'bash' and result['exit_code'] == 0
    assert card.identity[0] == worker.request.run_id
    assert not card.timer.isActive()


def test_controller_denial_returns_tool_result_and_continues(qtbot, controller, tmp_path, ollama_server):
    begin_task(controller, tmp_path, ollama_server)
    card = pending_card(qtbot, controller)
    card.reject.click()
    wait_finished(qtbot, controller)
    assert not (tmp_path / 'result.txt').exists()
    assert card.status.text() == '已拒绝执行'
    result = json.loads(ollama_server.requests[1]['payload']['messages'][-1]['content'])
    assert result['error_type'] == 'confirmation_denied'


def test_controller_expiry_disables_ui_and_continues(qtbot, controller, tmp_path, ollama_server):
    now = [time.monotonic()]
    begin_task(controller, tmp_path, ollama_server, clock=lambda: now[0])
    card = pending_card(qtbot, controller)
    # Broker deadline is authoritative even before the one-second GUI timer.
    now[0] += 601
    card.approve.click()
    wait_finished(qtbot, controller)
    assert not (tmp_path / 'result.txt').exists()
    assert card.status.text() == '确认已过期，未执行'
    assert not card.approve.isEnabled()


@pytest.mark.parametrize('operation', ['stop', 'new_conversation', 'hide', 'clear', 'shutdown'])
def test_pending_confirmation_invalidated_on_lifecycle_change(qtbot, controller, tmp_path, ollama_server, operation):
    worker = begin_task(controller, tmp_path, ollama_server)
    card = pending_card(qtbot, controller)
    req = card.confirmation
    if operation == 'stop':
        card.stop.click()
    elif operation == 'new_conversation':
        controller._new_conversation()
    elif operation == 'hide':
        controller._dialog.hide()
    elif operation == 'clear':
        controller._dialog.clear_messages()
    else:
        controller._dialog.take_shutdown_workers()
    assert not card.approve.isEnabled()
    controller._on_command_decided(req, True)
    wait_finished(qtbot, controller)
    assert worker._cancelled.is_set()
    assert not (tmp_path / 'result.txt').exists()
    assert len(ollama_server.requests) == 1


def test_controller_auto_readonly_card_finished_after_step_advance(qtbot, controller, tmp_path, ollama_server):
    (tmp_path / 'notes.txt').write_text('readonly')
    begin_task(controller, tmp_path, ollama_server, command='cat notes.txt')
    wait_finished(qtbot, controller)
    card = next(iter(controller._dialog._tool_cards.values()))
    assert card.terminal and card.status.text() == '已完成'
    assert 'readonly' in card.output_view.toPlainText()
    # Tool card appears before the streamed final answer.
    layout = controller._dialog._msg_layout
    assert layout.indexOf(card) < layout.count() - 2


def test_active_tool_run_disables_auto_hide(qtbot, controller):
    dialog = controller._dialog
    dialog.show()
    dialog.set_auto_hide(True)
    dialog.begin_tool_run('task')
    with patch.object(dialog, 'isActiveWindow', return_value=False):
        dialog._apply_auto_hide()
    assert dialog.isVisible()
    dialog.finish_tool_run()


def test_running_command_stop_prevents_later_write(qtbot, controller, tmp_path, ollama_server):
    begin_task(controller, tmp_path, ollama_server, command='sleep 5; printf late > result.txt')
    card = pending_card(qtbot, controller)
    card.approve.click()
    qtbot.waitUntil(lambda: card.status.text() == '执行中', timeout=2000)
    card.stop.click()
    wait_finished(qtbot, controller)
    assert not (tmp_path / 'result.txt').exists()
    assert card.status.text() == '已停止' and card.terminal
    assert len(ollama_server.requests) == 1


def test_removed_card_cannot_approve_another_run(qtbot, controller, tmp_path, ollama_server):
    begin_task(controller, tmp_path, ollama_server)
    card = pending_card(qtbot, controller)
    old_request = card.confirmation
    controller._stop_worker()
    wait_finished(qtbot, controller)
    # No new shell task starts from a forged old GUI response.
    controller._dialog.begin_tool_run('another-run')
    controller._on_command_decided(old_request, True)
    assert not (tmp_path / 'result.txt').exists()
    assert not card.approve.isEnabled()
    controller._dialog.finish_tool_run()


def test_confirmation_status_has_visible_width(qtbot):
    card = ToolCard(started(), '/tmp/workspace')
    qtbot.addWidget(card)
    card.resize(296, 320)
    card.show()
    assert card.bind_confirmation(confirmation(card))
    qtbot.waitUntil(lambda: card.status.width() > 30)
    assert card.status.isVisible() and card.status.text() == '等待确认'


def test_empty_answer_hidden_until_content_or_error(controller):
    dialog = controller._dialog
    dialog.begin_assistant_stream()
    dialog.begin_tool_run('run')
    dialog.show_tool_event(started(), '/tmp/workspace')
    container = dialog._stream_container
    assert container.isHidden()
    dialog.append_stream_chunk('reply')
    dialog._flush_stream_buffer()
    assert not container.isHidden()
    dialog.clear_messages()
    dialog.begin_assistant_stream()
    dialog.begin_tool_run('run')
    dialog.show_tool_event(started(), '/tmp/workspace')
    container = dialog._stream_container
    assert container.isHidden()
    dialog.finalize_assistant_stream('', False, error='test failure')
    assert not container.isHidden()
