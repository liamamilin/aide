"""Audit lifecycle/retention, real Qt thread delivery, no paid tools or real keys."""
import json
import sqlite3
import threading
import time
from dataclasses import replace

import pytest

from ai_desktop.llm.chat_client import ChatClient, _payload
from ai_desktop.llm.events import ChatResult, ResultStatus
from ai_desktop.llm.ollama_protocol import ModelTurn, ToolCall
from ai_desktop.llm.run_loop import RunLoop
from ai_desktop.llm.run_types import RunContext, RunEvent, RunEventKind, ToolOutput, ToolSpec
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.services import audit_store as audit
from ai_desktop.services.run_audit import RunAudit
from ai_desktop.ui.run_history_dialog import RunHistoryDialog
from ai_desktop.utils import storage
from tests import test_request_results as request_fixtures

controller = request_fixtures.controller


def new_run(*, origin='chat', tool=None, executor=None):
    convo = storage.create_conversation('general_assistant')
    user = storage.save_message(convo.id, 'user', 'task')
    request = ChatClient().create_request([user], 'SYSTEM', conversation_id=convo.id,
                                          agent_id='general_assistant', origin=origin, think=False,
                                          options={'num_ctx': 8192, 'num_predict': 1024})
    tools = (ToolSpec.create(tool, executor or (lambda *_: ToolOutput('done'))),) if tool else ()
    ctx = RunContext.create(request, tools=tools, tools_admitted=bool(tool))
    audit.begin_run(ctx, user.id)
    return ctx, user


def event(ctx, kind, seq, *, call=None, payload=None, output=None, status=''):
    req = ctx.request
    return RunEvent(req.run_id, req.conversation_id, req.step_id, req.request_id, seq, kind,
                    call, tool_name='web_search' if call else '', arguments_json='{"query":"docs"}',
                    output=output, status=status, payload_json=json.dumps(payload or {}))


def completed_search(ctx):
    record = {'provider': 'exa', 'sources': [{'source_id': 'S1', 'title': 'Python',
              'url': 'https://docs.python.org/', 'excerpt': 'Documentation'}], 'error_type': '', 'duration': .1}
    audit.observe(event(ctx, RunEventKind.MODEL_STARTED, 1, payload={'input': [{'role': 'user', 'content': 'task'}]}))
    audit.observe(event(ctx, RunEventKind.MODEL_FINISHED, 2, status='succeeded',
                        payload={'output': {'content': '', 'thinking': 'plan', 'done_reason': 'stop'}}))
    audit.observe(event(ctx, RunEventKind.TOOL_STARTED, 3, call='call'))
    audit.observe(event(ctx, RunEventKind.TOOL_FINISHED, 4, call='call', status='succeeded',
                        output=ToolOutput(json.dumps(record))))
    audit.observe(event(ctx, RunEventKind.FINISHED, 5, status='succeeded'))
    return record


def generation(ctx, user, text='Docs [S1]'):
    message = storage.save_message(ctx.request.conversation_id, 'assistant', text)
    gen = storage.save_generation(user.id, ctx.request.request_id,
                                 config_snapshot={'run_id': ctx.request.run_id}, status='succeeded',
                                 answer=text, assistant_message_id=message.id, active=True)
    return gen, message


@pytest.mark.parametrize('origin', ['chat', 'action'])
def test_plain_chat_and_action_record_without_tool_grant(tmp_db, origin):
    ctx, _ = new_run(origin=origin)
    def model(request, payload, deadline):
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, 'answer',
                          turn=ModelTurn('answer', 'thinking', (), 'stop'), run_id=request.run_id,
                          step_id=request.step_id, conversation_id=request.conversation_id)
    RunLoop(ctx, threading.Event(), model, on_event=audit.observe).run(_payload(ctx.request, True))
    run = audit.list_runs(ctx.request.conversation_id)[0]
    assert run['origin'] == origin and run['config']['allowed_tools'] == []
    assert run['status'] == 'succeeded' and len(run['steps']) == 1
    assert run['steps'][0]['payload']['output']['thinking'] == 'thinking'
    assert run['steps'][0]['payload_expires_at'] == pytest.approx(run['steps'][0]['ended_at']+audit.RETENTION_SECONDS)


def test_redaction_does_not_mutate_model_context(tmp_db):
    secret = 'very-private-test-value'
    raw = json.dumps({'stdout': f'PARALLEL_API_KEY={secret}', 'stderr': '', 'exit_code': 0})
    ctx, _ = new_run(tool='bash', executor=lambda *_: ToolOutput(raw))
    calls = []
    def model(request, payload, deadline):
        calls.append(payload)
        turn = (ModelTurn('', '', (ToolCall('call', 'bash', '{"command":"cat .env"}'),), 'stop')
                if len(calls) == 1 else ModelTurn('done', '', (), 'stop'))
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, turn.content, turn=turn,
                          run_id=request.run_id, step_id=request.step_id, conversation_id=request.conversation_id)
    RunLoop(ctx, threading.Event(), model, on_event=audit.observe).run(_payload(ctx.request, True))
    assert secret in json.dumps(calls[-1])
    run = audit.list_runs(ctx.request.conversation_id)[0]
    assert len(run['steps']) == 3 and secret not in json.dumps(run)
    assert audit.MASK in json.dumps(run)
    assert run['steps'][1]['payload']['arguments_json'] == '{"command":"cat .env"}'


@pytest.mark.parametrize('raw,secret', [
    ('API_KEY="test-value"', 'test-value'), ('exa_api_key=local-example', 'local-example'),
    ('token=some-token', 'some-token'), ('{"password":"test-password"}', 'test-password'),
    ('Authorization: Bearer bearer-value', 'bearer-value'),
    ('https://user:pass@example.com/?api_key=local-key', 'user:pass'),
    ('sk-abcdefghijklmnopqrstuvwxyz', 'sk-abcdefghijklmnopqrstuvwxyz'),
    ('-----BEGIN PRIVATE KEY-----\nprivate-value\n-----END PRIVATE KEY-----', 'private-value'),
])
def test_redaction_fixed_patterns(raw, secret):
    output = audit.encode_payload({'text': raw})
    assert secret not in output and audit.MASK in output


def test_payload_bounded_valid_json_and_no_repr_leak():
    encoded = audit.encode_payload({'body': '中文"\\\n' * 50000})
    assert len(encoded.encode()) <= audit.MAX_PAYLOAD_BYTES and json.loads(encoded)['truncated']
    req = ChatClient().create_request([], '')
    ev = RunEvent(req.run_id, 0, req.step_id, req.request_id, 1, RunEventKind.MODEL_STARTED,
                  payload_json='secret-payload')
    assert 'secret-payload' not in repr(ev)


def test_model_audit_omits_image_bytes_and_records_length_status(tmp_db):
    ctx, _ = new_run()
    def model(request, payload, deadline):
        assert payload['messages'][0]['images'] == ['private-image-bytes']
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, 'partial',
                          turn=ModelTurn('partial', '', (), 'length'), run_id=request.run_id,
                          step_id=request.step_id, conversation_id=request.conversation_id)
    wire = _payload(ctx.request, True)
    wire['messages'][0]['images'] = ['private-image-bytes']
    result = RunLoop(ctx, threading.Event(), model, on_event=audit.observe).run(wire)
    assert result.status == ResultStatus.LIMITED
    run = audit.list_runs(ctx.request.conversation_id)[0]
    assert run['status'] == run['steps'][0]['status'] == 'limited'
    assert 'private-image-bytes' not in json.dumps(run)
    assert run['steps'][0]['payload']['input'][0]['image_count'] == 1


def test_duplicate_and_foreign_events_never_modify_steps(tmp_db):
    ctx, _ = new_run()
    start = event(ctx, RunEventKind.MODEL_STARTED, 1)
    assert audit.observe(start)
    assert not audit.observe(start)
    assert not audit.observe(replace(start, seq=2, conversation_id=-1))
    finish = event(ctx, RunEventKind.MODEL_FINISHED, 3, status='succeeded')
    assert not audit.observe(replace(finish, step_id='foreign'))
    assert audit.observe(finish)
    assert not audit.observe(replace(finish, seq=4, status='failed'))
    assert audit.list_runs(ctx.request.conversation_id)[0]['steps'][0]['status'] == 'succeeded'


def test_restart_marks_active_runs_interrupted_once(tmp_db):
    ctx, _ = new_run()
    audit.observe(event(ctx, RunEventKind.MODEL_STARTED, 1, payload={'input': ['text']}))
    storage.init_db()  # Same process initialization must not interrupt a live worker.
    assert audit.list_runs(ctx.request.conversation_id)[0]['status'] == 'running'
    storage._local.audit_recovered_db = None
    storage.init_db()  # Simulated new application session.
    run = audit.list_runs(ctx.request.conversation_id)[0]
    assert run['status'] == run['steps'][0]['status'] == 'interrupted'
    assert run['steps'][0]['ended_at'] and run['steps'][0]['payload_expires_at']
    previous = run['ended_at']
    storage.init_db()
    assert audit.list_runs(ctx.request.conversation_id)[0]['ended_at'] == previous


def test_expiry_purges_payloads_and_managed_backups_but_preserves_answers(tmp_db):
    ctx, user = new_run(tool='web_search')
    completed_search(ctx)
    gen, msg = generation(ctx, user)
    assert audit.sources_for_message(msg.id)['S1']['url'] == 'https://docs.python.org/'
    db = storage._conn()
    cutoff = time.time()-1
    with db:
        db.execute('UPDATE agent_steps SET payload_expires_at=?', (cutoff,))
    backup = storage._create_schema_backup(db, 5, 6)
    assert audit.cleanup() == 2
    assert audit.cleanup() == 0
    run = audit.list_runs(ctx.request.conversation_id)[0]
    assert all(s['payload'] is None and s['payload_purged_at'] for s in run['steps'])
    assert len(run['steps']) == 2 and run['status'] == 'succeeded'
    assert audit.sources_for_message(generation_id=gen.id) == {}
    assert storage.get_generation(gen.id).answer == 'Docs [S1]'
    assert '执行详情已过期' in audit.export_markdown(ctx.request.conversation_id)
    copy = sqlite3.connect(backup)
    assert copy.execute('SELECT COUNT(*) FROM agent_steps WHERE payload_json IS NOT NULL').fetchone()[0] == 0
    copy.close()


def test_active_run_is_not_purged_even_with_expired_step(tmp_db):
    ctx, _ = new_run()
    audit.observe(event(ctx, RunEventKind.MODEL_STARTED, 1, payload={'input': ['active']}))
    db = storage._conn()
    with db:
        db.execute('UPDATE agent_steps SET payload_expires_at=1')
    assert audit.cleanup() == 0
    assert audit.list_runs(ctx.request.conversation_id)[0]['steps'][0]['payload']


def test_delete_cascades_and_retries_locked_backup(tmp_db):
    ctx, _ = new_run(tool='web_search')
    completed_search(ctx)
    backup = storage._create_schema_backup(storage._conn(), 5, 6)
    locked = sqlite3.connect(backup)
    locked.execute('BEGIN IMMEDIATE')
    try:
        storage.delete_conversation(ctx.request.conversation_id)
        assert not storage._conn().execute('SELECT * FROM agent_runs').fetchall()
        assert not storage._conn().execute('SELECT * FROM agent_steps').fetchall()
        assert storage._conn().execute('SELECT run_id FROM audit_deletions').fetchone()[0] == ctx.request.run_id
    finally:
        locked.rollback()
        locked.close()
    audit.cleanup()
    assert not storage._conn().execute('SELECT * FROM audit_deletions').fetchall()
    copy = sqlite3.connect(backup)
    assert copy.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0] == 0
    assert copy.execute('SELECT COUNT(*) FROM agent_steps').fetchone()[0] == 0
    copy.close()
    assert not audit.observe(event(ctx, RunEventKind.MODEL_STARTED, 9))


def test_backup_snapshot_interruption_does_not_extend_retention(tmp_db):
    ctx, _ = new_run()
    audit.observe(event(ctx, RunEventKind.MODEL_STARTED, 1, payload={'input': ['text']}))
    db = storage._conn()
    now = time.time()
    with db:
        db.execute('UPDATE agent_runs SET updated_at=?', (now-audit.RETENTION_SECONDS-10,))
    backup = storage._create_schema_backup(db, 5, 6)
    audit.cleanup(now=now)
    copy = sqlite3.connect(backup)
    assert copy.execute('SELECT status FROM agent_runs').fetchone()[0] == 'interrupted'
    assert copy.execute('SELECT payload_json FROM agent_steps').fetchone()[0] is None
    copy.close()
    assert audit.list_runs(ctx.request.conversation_id)[0]['status'] == 'running'


def test_no_cleaning_external_or_symlink_backup(tmp_db, tmp_path):
    ctx, _ = new_run(tool='web_search')
    completed_search(ctx)
    external = tmp_path/'external.sqlite3'
    copy = sqlite3.connect(external)
    storage._conn().backup(copy)
    copy.close()
    managed = storage.DB_PATH.parent/'backups'
    managed.mkdir(exist_ok=True)
    link = managed/f'{storage.DB_PATH.stem}.schema-5-to-6.symlink.sqlite3'
    link.symlink_to(external)
    try:
        audit.cleanup(now=time.time()+audit.RETENTION_SECONDS+1)
        copy = sqlite3.connect(external)
        assert copy.execute('SELECT COUNT(*) FROM agent_steps WHERE payload_json IS NOT NULL').fetchone()[0] == 2
        copy.close()
    finally:
        link.unlink()


def test_generation_binding_and_redacted_source_url_not_activated(tmp_db):
    ctx, user = new_run(tool='web_search')
    completed_search(ctx)
    gen, msg = generation(ctx, user)
    assert audit.list_runs(ctx.request.conversation_id)[0]['generation_id'] == gen.id
    assert audit.sources_for_message(msg.id)['S1']['expires_at'] > time.time()
    # Redacted URLs are not transformed into usable but incorrect links.
    db = storage._conn()
    row = db.execute('SELECT id,payload_json FROM agent_steps WHERE kind="tool"').fetchone()
    payload = json.loads(row['payload_json'])
    output = json.loads(payload['output'])
    output['sources'][0]['url'] = 'https://docs.python.org/?api_key=example-private'
    payload['output'] = json.dumps(output)
    with db:
        db.execute('UPDATE agent_steps SET payload_json=? WHERE id=?', (audit.encode_payload(payload), row['id']))
    assert audit.sources_for_message(msg.id) == {}


def test_read_only_history_restores_card_without_worker_or_commands(qtbot, tmp_db, monkeypatch):
    ctx, _ = new_run(tool='web_search')
    completed_search(ctx)
    def fail(*args):
        raise AssertionError('History must not execute')
    monkeypatch.setattr(RunWorker, 'start', fail)
    dialog = RunHistoryDialog(ctx.request.conversation_id)
    qtbot.addWidget(dialog)
    dialog.show()
    assert len(dialog.cards) == 1 and dialog.cards[0].terminal
    card = dialog.cards[0]
    assert not card.stop.isEnabled() and not card.approve.isEnabled()
    assert card.status.text() == '找到 1 个来源'
    card.stop.click()
    card.approve.click()
    dialog.reload()
    assert len(dialog.cards) == 1


def test_qt_worker_audit_is_delivered_on_gui_thread(qtbot, tmp_db, monkeypatch, ollama_server):
    convo = storage.create_conversation('general_assistant')
    user = storage.save_message(convo.id, 'user', 'hi')
    worker = RunWorker([user], '', conversation_id=convo.id, agent_id='general_assistant', think=False)
    recorder = RunAudit(worker.context, user.id)
    threads = []
    original = audit.observe
    def record(ev):
        threads.append(threading.get_ident())
        return original(ev)
    monkeypatch.setattr(audit, 'observe', record)
    worker.run_event.connect(recorder.observe)
    worker.done.connect(recorder.complete)
    ollama_server.enqueue({'message': {'role': 'assistant', 'content': 'hello'}, 'done': True})
    with qtbot.waitSignal(worker.done, timeout=3000):
        worker.start()
    assert worker.wait(2000)
    qtbot.waitUntil(lambda: audit.list_runs(convo.id)[0]['status'] == 'succeeded')
    assert threads and set(threads) == {threading.get_ident()}
    worker.deleteLater()
    recorder.deleteLater()


def test_audit_failure_is_finite_and_once(qtbot, tmp_db, monkeypatch, caplog):
    ctx, user = new_run()
    # Use a distinct run because the fixture already inserted its first one.
    ctx = replace(ctx, request=replace(ctx.request, run_id='audit-failure'))
    recorder = RunAudit(ctx, user.id)
    def fail(*args):
        raise RuntimeError('private-native-detail')
    monkeypatch.setattr(audit, 'observe', fail)
    seen = []
    recorder.failed.connect(seen.append)
    recorder.observe(event(ctx, RunEventKind.MODEL_STARTED, 1))
    recorder.observe(event(ctx, RunEventKind.MODEL_STARTED, 2))
    assert seen == ['audit-failure'] and 'private-native-detail' not in caplog.text
    recorder.deleteLater()


def test_controller_chat_audit_and_export(qtbot, controller, ollama_server):
    ollama_server.enqueue({'message': {'role': 'assistant', 'content': 'ordinary answer'}, 'done': True})
    controller._on_user_message('ordinary task', [])
    qtbot.waitUntil(lambda: controller._worker is None, timeout=3000)
    run = audit.list_runs(controller._convo_id)[0]
    assert run['status'] == 'succeeded' and run['generation_id']
    assert run['config']['allowed_tools'] == [] and len(run['steps']) == 1
    controller._on_export_requested()
    from PyQt5.QtWidgets import QApplication
    assert '运行记录' in QApplication.clipboard().text()


def test_controller_stale_worker_closes_own_audit_without_polluting_new_chat(qtbot, controller, ollama_server):
    old = ollama_server.enqueue(before_headers=True)
    controller._on_user_message('old task', [])
    old_id = controller._convo_id
    qtbot.waitUntil(old.received.is_set, timeout=3000)
    controller._new_conversation()
    ollama_server.enqueue({'message': {'role': 'assistant', 'content': 'new answer'}, 'done': True})
    controller._on_user_message('new task', [])
    new_id = controller._convo_id
    qtbot.waitUntil(lambda: controller._worker is None and not controller._stale_workers, timeout=3000)
    assert old_id != new_id and audit.list_runs(old_id)[0]['status'] == 'cancelled'
    assert audit.list_runs(new_id)[0]['status'] == 'succeeded'
    assert all('old task' not in m.content for m in controller._messages)


def test_historical_answer_sources_and_versions_are_isolated(qtbot, tmp_db, monkeypatch):
    from ai_desktop import config
    from ai_desktop.ui.chat_dialog import ChatDialog
    ctx, user = new_run(tool='web_search')
    completed_search(ctx)
    gen, message = generation(ctx, user)
    dialog = ChatDialog(config.AGENTS, config.AGENTS[0])
    qtbot.addWidget(dialog)
    dialog.add_assistant_message('Docs [S1]', regen_available=True,
                                 sources=audit.sources_for_message(message.id))
    from ai_desktop.ui.fluent import BodyLabel
    label = dialog._msg_container.findChildren(BodyLabel, 'message_bubble')[-1]
    opened = []
    monkeypatch.setattr('ai_desktop.ui.tool_card.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    label.linkActivated.emit('source://S1')
    assert opened == ['https://docs.python.org/']
    assert dialog.show_generation(999, 'Another [S1]', sources={})
    label.linkActivated.emit('source://S1')
    assert len(opened) == 1 and 'source://S1' not in label.text()
    assert dialog.show_generation(gen.id, gen.answer, sources=audit.sources_for_message(generation_id=gen.id))
    label.linkActivated.emit('source://S1')
    assert len(opened) == 2
    label._search_sources['S1']['expires_at'] = time.time()-1
    label.linkActivated.emit('source://S1')
    assert len(opened) == 2 and not label._search_sources


def test_visible_tool_card_expiry_clears_content_and_disables_links(qtbot, tmp_db, monkeypatch):
    ctx, _ = new_run(tool='web_search')
    record = completed_search(ctx)
    from ai_desktop.ui.tool_card import ToolCard
    start = event(ctx, RunEventKind.TOOL_STARTED, 3, call='call')
    card = ToolCard(start)
    qtbot.addWidget(card)
    card.update_event(replace(start, kind=RunEventKind.TOOL_FINISHED, output=ToolOutput(json.dumps(record))))
    button = card.source_buttons[0]
    card.payload_expires_at = time.time()-1
    opened = []
    monkeypatch.setattr('ai_desktop.ui.tool_card.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    button.click()
    assert not opened and not card.command_view.toPlainText() and not card.output_view.toPlainText()
    assert not card.source_buttons and '过期' in card.reason.text()


def test_schema_four_upgrade_backups_preserve_chat_without_invented_runs(tmp_path, monkeypatch):
    path = tmp_path/'chat_history.db'
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    for version in range(4):
        storage._MIGRATIONS[version](db)
    db.execute('PRAGMA user_version=4')
    db.execute('INSERT INTO conversations(title,agent_id,created_at) VALUES("old","general_assistant",1)')
    db.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(1,"user","legacy",1)')
    db.commit()
    db.close()
    monkeypatch.setattr(storage, 'DB_PATH', path)
    monkeypatch.setattr(storage, '_local', threading.local())
    storage.init_db()
    assert storage._schema_version(storage._conn()) == 5
    assert storage.get_conversation(1).messages[0].content == 'legacy'
    assert audit.list_runs(1) == []
    backup = sqlite3.connect(next((tmp_path/'backups').glob('*.sqlite3')))
    assert backup.execute('PRAGMA user_version').fetchone()[0] == 4
    assert backup.execute('SELECT content FROM messages').fetchone()[0] == 'legacy'
    backup.close()


def test_selected_answer_and_its_sources_stay_paired_after_history_reload(qtbot, controller, tmp_db):
    from ai_desktop.ui.fluent import BodyLabel
    ctx, user = new_run(tool='web_search')
    completed_search(ctx)
    first, _ = generation(ctx, user, 'First [S1]')
    # A second run for the same user has its own S1.
    second_ctx = replace(ctx, request=replace(ctx.request, run_id='second-run', request_id='second-request'))
    audit.begin_run(second_ctx, user.id)
    completed_search(second_ctx)
    db = storage._conn()
    row = db.execute('SELECT id,payload_json FROM agent_steps WHERE run_id="second-run" AND kind="tool"').fetchone()
    payload = json.loads(row['payload_json'])
    record = json.loads(payload['output'])
    record['sources'][0]['url'] = 'https://www.python.org/doc/'
    payload['output'] = json.dumps(record)
    with db:
        db.execute('UPDATE agent_steps SET payload_json=? WHERE id=?', (audit.encode_payload(payload), row['id']))
    second, _ = generation(second_ctx, user, 'Second [S1]')
    storage.set_active_generation(first.id)
    controller._convo_id = ctx.request.conversation_id
    controller._messages = storage.get_conversation(controller._convo_id).messages
    controller._render_messages()
    labels = controller._dialog._msg_container.findChildren(BodyLabel, 'message_bubble')
    answers = [label for label in labels if getattr(label, '_markdown_source', None)]
    assert len(answers) == 1 and answers[0]._markdown_source == 'First [S1]'
    assert answers[0]._search_sources['S1']['url'] == 'https://docs.python.org/'
    controller._on_generation_selected(second.id)
    assert answers[0]._search_sources['S1']['url'] == 'https://www.python.org/doc/'
    controller._render_messages()
    answers = [label for label in controller._dialog._msg_container.findChildren(BodyLabel, 'message_bubble')
               if getattr(label, '_markdown_source', None)]
    assert answers[-1]._markdown_source == 'Second [S1]'
    assert answers[-1]._search_sources['S1']['url'] == 'https://www.python.org/doc/'
