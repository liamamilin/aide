#!/usr/bin/env python3
"""Opt-in native Qt / real local 9B acceptance, isolated from user data.

Uses selected local models and isolated data. Default cases never access search
credentials; an explicit --allow-paid-search and one search case permit at most
one live provider request. Run: python3 scripts/harness_acceptance.py
"""
import argparse
import faulthandler
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from ai_desktop.diagnostics.search_acceptance import PAID_CASES, validate_paid_case

DIAGNOSTIC_MODULE = 'ai_desktop.diagnostics.harness_acceptance'
CASES = ('ordinary_send_and_regenerate', 'bash_readonly', 'bash_approve', 'bash_reject', 'bash_hide',
         'cancel_then_new_conversation', 'restart_and_readonly_history', 'window_layout_and_restore',
         'model_switch_tools', 'settings_tab_navigation', 'settings_task_limits', 'task_model_settings',
         'all_agent_tools', 'answer_layout', 'selection_hotkey', 'desktop_recovery',
         'conversation_lifecycle', 'tool_settings_inheritance') + PAID_CASES + tuple(
    'action_'+mode+'_'+action for mode in ('current', 'new')
    for action in ('translate', 'explain', 'summarize', 'rewrite'))


def isolated_root(environ=None):
    """Require the launcher-owned temporary root before any database is opened."""
    environ = os.environ if environ is None else environ
    value = environ.get('AIDE_ACCEPTANCE_ROOT', '')
    if not value:
        raise ValueError('Use scripts/harness_acceptance.py to launch isolated acceptance')
    root = Path(value).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith('aide-acceptance-'):
        raise ValueError('Acceptance requires a launcher-owned temporary directory')
    if not root.is_dir():
        raise ValueError('Acceptance directory does not exist')
    for variable, child in [('AIDE_DATA_DIR', 'data'), ('AIDE_LOG_DIR', 'logs')]:
        if not environ.get(variable) or Path(environ[variable]).resolve() != root/child:
            raise ValueError('Acceptance data and logs must be isolated')
    return root


def child_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--harness-acceptance']
    return [sys.executable, '-m', DIAGNOSTIC_MODULE]


def controller_class():
    # The PyInstaller entry executes main.py as __main__. Reuse that module's
    # controller instead of importing/initializing a second entry module.
    entry = sys.modules.get('__main__')
    if getattr(sys, 'frozen', False) and hasattr(entry, 'ChatController'):
        return entry.ChatController
    from ai_desktop.main import ChatController
    return ChatController


def resume_probe(directory, conversation_id, interrupted_id):
    root = isolated_root()
    if Path(directory).resolve() != root:
        raise ValueError('Restart must use the same isolated directory')
    from PyQt5.QtWidgets import QApplication

    ChatController = controller_class()
    from ai_desktop.services import audit_store
    from ai_desktop.ui.fluent import initialize
    app = QApplication([])
    initialize()
    ctl = ChatController()
    before = audit_store.list_runs(conversation_id)
    ctl._restore_last = False
    ctl._show_dialog()
    ctl._on_conversation_selected(conversation_id)
    after = audit_store.list_runs(conversation_id)
    assert ctl._worker is None and ctl._task_authorization is None
    assert len(before) == len(after)
    assert next(run for run in after if run['run_id'] == interrupted_id)['status'] == 'interrupted'
    assert ctl._dialog is not None and ctl._messages
    ctl.stop()
    app.processEvents()
    print(json.dumps({'restart': 'passed', 'history_executed_tools': False, 'authorization_restored': False}))


def acceptance(output, only=None, model='qwen3.5:9b-mlx', *, allow_paid_search=False, search_agent='code_expert'):
    validate_paid_case(only, allow_paid_search)
    root = isolated_root()
    from PyQt5.QtCore import QEventLoop, QTimer
    from PyQt5.QtWidgets import QApplication

    from ai_desktop import config
    from ai_desktop.llm.chat_client import ChatClient
    from ai_desktop.llm.run_types import RunContext

    # No hotkey listeners, selection capture or controller.start() in this diagnostic.
    # Actual FloatButton, ChatDialog, task panel, workers and native windows are used.
    ChatController = controller_class()
    from ai_desktop.llm.model_options import global_options
    from ai_desktop.services import audit_store
    from ai_desktop.services.model_profiles import ModelProfile
    from ai_desktop.ui.fluent import initialize
    from ai_desktop.ui.task_dialog import TaskDialog
    from ai_desktop.utils import storage

    assert Path(storage.DB_PATH).resolve() == root/'data'/'chat_history.db', 'Database imported before isolation'
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    initialize()
    config.OLLAMA_BASE_URL = 'http://localhost:11434'
    config.OLLAMA_MODEL = model
    config.OLLAMA_THINK = False
    config.OLLAMA_NUM_CTX = 8192
    config.OLLAMA_NUM_PREDICT = 512
    config.OLLAMA_TEMPERATURE = 0
    ctl = ChatController()
    ctl._restore_last = False
    ctl._show_dialog()
    ctl._dialog.set_auto_hide(False)
    workspace = root/'workspace'
    workspace.mkdir()
    (workspace/'note.txt').write_text('verification_code: ACCEPTANCE-READ-5741\n')
    report = {'model': model,
              'real_local_model': only not in {'restart_and_readonly_history', 'window_layout_and_restore',
                                               'settings_tab_navigation', 'settings_task_limits',
                                               'task_model_settings', 'answer_layout', 'selection_hotkey',
                                               'desktop_recovery', 'conversation_lifecycle',
                                               'tool_settings_inheritance'},
              'native_qt': app.platformName() not in {'offscreen', 'minimal'},
              'qt_platform': app.platformName(),
              'paid_search_authorized': allow_paid_search, 'paid_search_used': False,
              'search_http_attempts': 0, 'search_http_results': [], 'user_data_isolated': True,
              'execution': 'frozen-app' if getattr(sys, 'frozen', False) else 'source',
              'global_hotkeys_started': False, 'complete': False, 'cases': []}

    def wait(predicate, action=None, timeout=45):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(20)
        started = time.monotonic()
        errors = []
        def poll():
            try:
                if action:
                    action()
                if predicate():
                    loop.quit()
                elif time.monotonic()-started >= timeout:
                    errors.append('Acceptance deadline exceeded')
                    ctl._stop_worker()
                    loop.quit()
            except Exception as exc:
                errors.append(str(exc))
                ctl._stop_worker()
                loop.quit()
        timer.timeout.connect(poll)
        timer.start()
        loop.exec_()
        timer.stop()
        if errors:
            raise AssertionError(errors[0])
        return round(time.monotonic()-started, 3)

    def idle(action=None):
        return wait(lambda: ctl._pending_task is None and ctl._worker is None and not ctl._stale_workers, action)

    def case(name, callback):
        if only is not None and name != only:
            return
        started = time.monotonic()
        try:
            details = callback() or {}
            record = {'case': name, 'status': 'passed', **details}
        except Exception as exc:
            ctl._stop_worker()
            idle()
            record = {'case': name, 'status': 'failed', 'error': (type(exc).__name__+': '+str(exc))[:300]}
        record['seconds'] = round(time.monotonic()-started, 3)
        report['cases'].append(record)
        if output:
            destination = Path(output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'case': name, 'status': record['status']}), flush=True)

    def send(text):
        ctl._dialog.set_input_text(text)
        ctl._dialog._send_btn.click()

    def select_role(agent_id):
        ctl._dialog.new_convo_requested.emit()
        agent = next(a for a in ctl._all_agents if a.id == agent_id)
        ctl._dialog.set_active_agent(agent)
        ctl._on_agent_changed(agent)
        ctl._dialog.show()

    def general():
        select_role('general_assistant')

    def answer():
        return next((msg.content for msg in reversed(ctl._messages) if msg.role == 'assistant'), '')

    def enable(policy='readonly_auto', agent_id='general_assistant', search_provider=None):
        select_role(agent_id)
        guard = QTimer()
        guard.setSingleShot(True)
        opened = []
        def configure():
            panels = ctl._dialog.findChildren(TaskDialog)
            if not panels:
                return
            panel = panels[-1]
            if panel in opened:
                return
            opened.append(panel)
            panel._bash.setChecked(search_provider is None)
            panel._search.setChecked(search_provider is not None)
            if search_provider:
                panel._provider.setCurrentIndex(panel._provider.findData(search_provider))
            panel._workspace.setText(str(workspace))
            panel._policy.setCurrentIndex(panel._policy.findData(policy))
            panel._enable.click()
        chooser = QTimer()
        chooser.setInterval(20)
        chooser.timeout.connect(configure)
        guard.timeout.connect(lambda: [p.reject() for p in opened])
        chooser.start()
        guard.start(8000)
        try:
            ctl._dialog.task_settings_requested.emit()
        finally:
            chooser.stop()
            guard.stop()
        assert ctl._task_authorization is not None, 'Task panel did not grant tools'
        assert ctl._task_authorization.agent_id == agent_id

    def live_search(provider):
        from PyQt5.QtWidgets import QLabel

        from ai_desktop.diagnostics.search_acceptance import SearchTrace
        from ai_desktop.services.web_search import normalized_sources
        from ai_desktop.ui.tool_card import ToolCard

        config.SEARCH_MAX_RESULTS = 1
        config.SEARCH_TIMEOUT = 30
        config.SEARCH_PARALLEL_MODE = 'basic'
        config.TASK_MAX_MODEL_ROUNDS = 2
        config.TASK_MAX_TOOL_CALLS = 1
        config.TASK_MAX_SEARCH_CALLS = 1
        if search_agent == 'acceptance-researcher':
            ctl._on_custom_agents_saved([{'id': search_agent, 'name': '资料研究', 'icon': '📚',
                                         'system_prompt': '你是资料研究助手。准确回答材料中的事实，用中文简明回复。'}])
        enable(agent_id=search_agent, search_provider=provider)
        assert ctl._task_authorization.execution is None
        with SearchTrace() as trace:
            try:
                send('请调用 web_search 搜索 Python official documentation，'
                     '只搜索一次。根据搜索结果用一句中文说明 Python 官方文档的用途，'
                     '引用真实来源编号 [S1]。不要自行编造网址。')
                idle()
            finally:
                report['paid_search_used'] = trace.attempts > 0
                report['search_http_attempts'] = trace.attempts
                report['search_http_results'] = trace.results
        run = audit_store.list_runs(ctl._convo_id)[0]
        searches = [step for step in run['steps'] if step['tool_name'] == 'web_search']
        assert len(searches) == 1, 'Model did not issue exactly one search'
        tool_result = json.loads(searches[0]['payload']['output'])
        assert not tool_result['error_type'], f'Search failed: {tool_result["error_type"]}'
        assert trace.attempts == 1 and len(trace.results) == 1 and trace.results[0]['http_status'] == 200
        assert run['status'] == 'succeeded' and run['agent_id'] == search_agent
        assert run['config']['model'] == model and run['config']['allowed_tools'] == ['web_search']
        assert run['config']['search']['provider'] == provider
        model_steps = [s for s in run['steps'] if s['kind'] == 'model']
        assert len(model_steps) == 2
        assert all(step['payload']['input'][0]['content'].startswith(
            ctl._active_agent.system_prompt+'\n\n[工具使用规则]') for step in model_steps), 'Role prompt changed'
        sources = normalized_sources(tool_result)
        assert sources and sources[0]['source_id'] == 'S1', 'Search returned no usable source'
        assert '[S1]' in answer(), 'Final answer omitted the verified source'
        text = answer()
        labels = ctl._dialog.findChildren(QLabel)
        label = next(label for label in labels if getattr(label, '_markdown_source', '') == text)
        assert 'source://S1' in label.text() and label._search_sources['S1']['url'] == sources[0]['url']
        cards = ctl._dialog.findChildren(ToolCard)
        card = next(card for card in cards if card.tool_name == 'web_search')
        assert card.source_buttons and card.source_buttons[0].toolTip() == sources[0]['url']
        assert '找到 1 个来源' in card.status.text() and provider.title() in card.summary.text()
        from ai_desktop.diagnostics.answer_layout import fits, measure

        answer_card = label.parentWidget()
        wait(lambda: fits(label, answer_card), timeout=3)
        answer_geometry = measure(label)
        report['search_validation'] = {'agent_id': search_agent, 'provider': provider,
                                       'role_prompt_preserved': True, 'model_steps': 2,
                                       'verified_citation': True, 'answer_layout': answer_geometry}
        conversation_id = ctl._convo_id
        preview = None
        if output:
            preview = Path(output).parent/'previews'/(Path(output).stem+'.png')
            preview.parent.mkdir(parents=True, exist_ok=True)
            assert ctl._dialog.grab().save(str(preview))
        previous_labels = set(labels)
        ctl._on_conversation_selected(conversation_id)
        assert ctl._task_authorization is None and ctl._worker is None
        from ai_desktop.diagnostics.answer_layout import restored_answer

        wait(lambda: restored_answer(ctl._dialog, text, previous_labels) is not None, timeout=3)
        restored = restored_answer(ctl._dialog, text, previous_labels)
        assert 'source://S1' in restored.text() and restored._search_sources['S1']['url'] == sources[0]['url']
        wait(lambda: restored.height() >= restored.heightForWidth(restored.width()), timeout=3)
        assert len(audit_store.list_runs(conversation_id)) == 1 and trace.attempts == 1
        return {'agent_id': search_agent, 'provider': provider, 'model_steps': 2, 'role_prompt_preserved': True,
                'search_http_attempts': trace.attempts, 'source_ids': [s['source_id'] for s in sources],
                'source_urls': [s['url'] for s in sources], 'verified_citation': True,
                'history_citation_restored': True, 'history_tools_executed': False,
                'answer_layout': answer_geometry, 'history_answer_layout': measure(restored),
                'preview': str(preview) if preview else None}

    def all_agent_tools():
        ctl._on_custom_agents_saved([{'id': 'acceptance-researcher', 'name': '资料研究', 'icon': '📚',
                                     'system_prompt': '你是资料研究助手。准确回答材料中的事实，用中文简明回复。'}])
        roles = []
        for agent_id in ['general_assistant', 'code_expert', 'translator', 'summarizer',
                         'polisher', 'acceptance-researcher']:
            enable(agent_id=agent_id)
            agent = ctl._active_agent
            send('请先使用 bash 执行 cat note.txt，读取并回复文件中的 verification_code。'
                 '不要根据记忆猜测，不要修改文件。')
            idle()
            run = audit_store.list_runs(ctl._convo_id)[0]
            assert run['agent_id'] == agent_id and run['status'] == 'succeeded', f'{agent_id}: {run["status"]}'
            assert run['config']['model'] == model and run['config']['options'] == global_options(), agent_id
            assert 'ACCEPTANCE-READ-5741' in answer(), f'{agent_id}: missing marker, answer={answer()[:160]}'
            assert any(step['tool_name'] == 'bash' for step in run['steps']), f'{agent_id}: no Bash call'
            steps = [step for step in run['steps'] if step['kind'] == 'model']
            assert all(step['payload']['input'][0]['content'].startswith(
                agent.system_prompt+'\n\n[工具使用规则]') for step in steps), f'{agent_id}: changed role prompt'
            roles.append({'agent_id': agent_id, 'model_steps': len(steps), 'actual_bash': True,
                          'role_prompt_preserved': True})
            ctl._dialog.new_convo_requested.emit()
            assert ctl._task_authorization is None
        return {'roles': roles, 'new_conversation_clears_authorization': True}

    def ordinary():
        general()
        send('仅回复英文标记 ACCEPTANCE-CHAT-3208，不添加其他内容。')
        idle()
        assert 'ACCEPTANCE-CHAT-3208' in answer()
        user = next(msg for msg in reversed(ctl._messages) if msg.role == 'user')
        ctl._dialog.regenerate_requested.emit()
        idle()
        assert 'ACCEPTANCE-CHAT-3208' in answer()
        assert len(storage.list_generations(user.id)) == 2
        assert len([msg for msg in ctl._messages if msg.role == 'user']) == 1
        assert all(not run['config']['allowed_tools'] for run in audit_store.list_runs(ctl._convo_id))
        return {'requests': 2, 'duplicate_user_messages': False}

    def action_case(action_id, mode):
        general()
        if mode == 'current':
            convo = storage.create_conversation('general_assistant', 'acceptance')
            storage.save_message(convo.id, 'user', '只回复 READY。')
            storage.save_message(convo.id, 'assistant', 'READY')
            ctl._on_conversation_selected(convo.id)
        old_convo = ctl._convo_id
        action = ctl._action_service.get(action_id)
        ctl._profile_mgr.save(ModelProfile('acceptance', '验收', model, False, 0, 512))
        ctl._action_service.save(replace(action, profile_id='acceptance'))
        material = 'The small garden is quiet. A student reads a book under the tree.'
        ctl._dialog.action_requested.emit(action_id, material, mode)
        idle()
        assert answer(), 'No Action answer'
        assert (ctl._convo_id == old_convo) == (mode == 'current')
        visible = ctl._active_agent.id
        user = next(msg for msg in reversed(ctl._messages) if msg.role == 'user')
        ctl._dialog.regenerate_requested.emit()
        idle()
        versions = storage.list_generations(user.id)
        assert len(versions) == 2 and versions[-1].status == 'succeeded'
        expected = ctl._action_service.build_request_plan(action_id, material, ctl._all_agents)
        runs = audit_store.list_runs(ctl._convo_id)
        assert len(runs) == 2 and ctl._active_agent.id == visible
        for run in runs:
            assert run['origin'] == 'action' and not run['config']['allowed_tools']
            assert run['config']['model'] == model and run['config']['options']['num_predict'] == 512
            assert len(run['steps']) == 1 and run['status'] == 'succeeded'
            assert run['steps'][0]['payload']['input'][0]['content'] == expected.system_prompt
        return {'requests': 2, 'context_mode': mode, 'tools': [], 'agent_unchanged_on_retry': True}

    def readonly():
        enable()
        send('请使用 bash 执行 cat note.txt，读取并回复 verification_code。不要修改文件。')
        idle()
        run = audit_store.list_runs(ctl._convo_id)[0]
        assert run['status'] == 'succeeded' and 'ACCEPTANCE-READ-5741' in answer()
        assert run['config']['options'] == global_options()
        assert any(step['tool_name'] == 'bash' for step in run['steps'])
        return {'status': 'passed', 'run_status': run['status'], 'model_steps':
                len([step for step in run['steps'] if step['kind'] == 'model'])}

    def write_case(decision):
        enable('confirm_all')
        filename = decision+'.txt'
        artifact = workspace/filename
        command = f"printf 'ACCEPTANCE-WRITE-5741' > {filename}"
        seen = set()
        unexpected = []
        def decide():
            for card in list(ctl._dialog._tool_cards.values()):
                if not card.pending or card.confirmation.id in seen:
                    continue
                seen.add(card.confirmation.id)
                request = card.confirmation
                if request.command != command or request.workspace != str(workspace.resolve()):
                    unexpected.append('Unexpected command denied')
                    card.reject.click()
                    ctl._stop_worker()
                elif decision == 'hide':
                    ctl._dialog.hide()
                else:
                    getattr(card, decision).click()
        send(f'请用 bash 执行这条精确命令，不要改写，不要执行其他命令：{command}。如果被拒绝，停止尝试。')
        idle(decide)
        assert seen and not unexpected, 'No expected confirmation, or unexpected command'
        assert artifact.exists() == (decision == 'approve')
        if decision == 'approve':
            assert artifact.read_text() == 'ACCEPTANCE-WRITE-5741'
        run = audit_store.list_runs(ctl._convo_id)[0]
        assert run['status'] == ('cancelled' if decision == 'hide' else 'succeeded')
        return {'confirmations': len(seen), 'file_written': artifact.exists(), 'run_status': run['status']}

    def cancel():
        general()
        send('请写一篇很长的英文故事，分成二十个部分，详细描述一次旅程。')
        wait(lambda: bool(ctl._response_text))
        old_conversation = ctl._convo_id
        ctl._dialog.new_convo_requested.emit()
        idle()
        assert ctl._convo_id == 0 and not ctl._messages and ctl._task_authorization is None
        runs = audit_store.list_runs(old_conversation)
        assert runs and runs[0]['status'] == 'cancelled'
        send('仅回复标记 AFTER-CANCEL-8049。')
        idle()
        assert 'AFTER-CANCEL-8049' in answer()
        assert len([msg for msg in ctl._messages if msg.role == 'user']) == 1
        return {'old_run_status': 'cancelled', 'new_context_clean': True}

    def window_controls():
        from PyQt5.QtCore import QSize
        from PyQt5.QtWidgets import QWidget
        general()
        dialog = ctl._dialog
        # Native Qt layout and app-owned pixels only; no screen capture/TCC.
        dialog.set_input_text('Window layout fixture')
        sizes = []
        for size in [QSize(400, 460), QSize(520, 800)]:
            dialog.resize(size)
            app.processEvents()
            assert dialog._send_btn.isVisible() and dialog._input.isVisible()
            assert dialog._input.isEnabled() and dialog._send_btn.isEnabled()
            for widget in [dialog._input, dialog._send_btn]:
                position = widget.mapTo(dialog, widget.rect().topLeft())
                assert dialog.rect().contains(position)
                assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
            sizes.append([dialog.width(), dialog.height()])
            if output:
                destination = Path(output).parent/'previews'
                destination.mkdir(parents=True, exist_ok=True)
                assert QWidget.grab(dialog).save(str(destination/f'window-{size.width()}x{size.height()}.png'))
        normal = dialog.geometry()
        dialog._expand_btn.click()
        app.processEvents()
        assert dialog._expanded_to_screen and dialog.geometry() == dialog._current_screen_geometry()
        dialog._expand_btn.click()
        app.processEvents()
        assert not dialog._expanded_to_screen and dialog.geometry() == normal
        dialog.scale_window(.85)
        dialog.reset_window_size()
        app.processEvents()
        assert dialog.size() == dialog._default_size_for_area(dialog._current_screen_geometry())
        assert dialog._input.toPlainText() == 'Window layout fixture'
        assert ctl._worker is None and not audit_store.list_runs(ctl._convo_id)
        return {'native_layout_sizes': sizes, 'expanded_and_restored': True,
                'default_size_restored': True, 'draft_preserved': True,
                'screen_capture_used': False, 'cross_app_focus_verified': False}

    def settings_tabs():
        from PyQt5.QtCore import QEvent, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent

        from ai_desktop.ui.settings_dialog import SettingsDialog

        # No credential methods are available: this case must never read or
        # write real keys, save settings, or run the paid search test.
        dialog = SettingsDialog({}, parent=ctl._dialog, credentials=object())
        visits = []
        saved = []
        dialog.settings_applied.connect(saved.append)
        try:
            dialog.show()
            app.processEvents()
            assert dialog._pivot.currentRouteKey() == 'model'
            assert dialog._widgets['base_url'].isVisibleTo(dialog)
            dialog._widgets['temperature'].setValue(0.35)
            for index in [1, 2, 3, 4, 0, 1]:
                route, _, title, _, start, _ = dialog.GROUPS[index]
                tab = dialog._pivot.widget(route)
                pos = QPointF(tab.rect().center())
                for event, buttons in [(QEvent.MouseButtonPress, Qt.LeftButton),
                                       (QEvent.MouseButtonRelease, Qt.NoButton)]:
                    QApplication.sendEvent(tab, QMouseEvent(event, pos, Qt.LeftButton, buttons, Qt.NoModifier))
                wait(lambda: dialog._pivot.slideAni.state() == 0, timeout=3)
                app.processEvents()
                assert dialog._pivot.currentRouteKey() == route
                assert dialog._pages.currentIndex() == index
                assert dialog._widgets[dialog.FIELDS[start][0]].isVisibleTo(dialog)
                visits.append({'route': route, 'title': title, 'index': index})
                if output:
                    preview = Path(output).parent/'previews'/('settings-'+route+'.png')
                    preview.parent.mkdir(parents=True, exist_ok=True)
                    assert dialog.grab().save(str(preview))
            assert dialog._widgets['temperature'].value() == 0.35
            assert not saved
            return {'mouse_click_visits': visits, 'draft_preserved': True,
                    'settings_saved': False, 'screen_capture_used': False}
        finally:
            dialog.close()

    def settings_limits():
        from dataclasses import asdict

        from ai_desktop.llm.run_worker import RunWorker
        from ai_desktop.services.execution_context import ExecutionSnapshot
        from ai_desktop.ui.settings_dialog import SettingsDialog

        values = {'search_max_results': 99, 'task_max_tool_calls': 39,
                  'task_max_search_calls': 17, 'task_max_model_rounds': 25}
        dialog = SettingsDialog(ctl._settings.current(), parent=ctl._dialog, credentials=object())
        dialog.settings_applied.connect(ctl._on_settings_applied)
        restored = None
        worker = None
        try:
            dialog.show()
            app.processEvents()
            for key, value in values.items():
                assert dialog._widgets[key].minimum() == 1 and dialog._widgets[key].maximum() == 99
                dialog._widgets[key].setValue(value)
            for route in ['execution', 'search']:
                dialog._pivot.setCurrentItem(route)
                wait(lambda: dialog._pivot.slideAni.state() == 0, timeout=3)
                if route == 'execution':
                    scroll = dialog._pages.currentWidget()
                    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                app.processEvents()
                if output:
                    preview = Path(output).parent/'previews'/('settings-limits-'+route+'.png')
                    preview.parent.mkdir(parents=True, exist_ok=True)
                    assert dialog.grab().save(str(preview))
            dialog._save_button.click()
            assert dialog.result() == dialog.Accepted
            for key, value in values.items():
                assert storage.get_setting(key) == str(value)
            ctl._settings.load()
            restored = SettingsDialog(ctl._settings.current(), credentials=object())
            assert all(restored._widgets[key].value() == value for key, value in values.items())
            worker = RunWorker([], 'fixture', agent_id='general_assistant', tools_admitted=True,
                               execution=ExecutionSnapshot.create(workspace))
            limits = asdict(worker.context.limits)
            assert limits['max_tool_calls'] == 39 and limits['max_search_calls'] == 17
            assert limits['max_model_rounds'] == 25 and worker.context.tools
            assert worker.context.search_settings is None  # No search executor or credentials are used.
            return {'saved_and_reloaded': values, 'worker_limits': limits,
                    'model_or_tools_executed': False, 'credential_access': False}
        finally:
            dialog.close()
            if restored:
                restored.close()
            if worker:
                worker.deleteLater()

    def model_switch_tools():
        enable()
        grant = ctl._task_authorization
        selected = []
        conversation = None
        for target in ['qwen3.5:9b-mlx', 'qwen3.8:27b-mlx']:
            assert target in ctl._dialog._models, 'Fixture model is not installed'
            ctl._dialog._model_combo.setCurrentText(target)
            assert ctl._model == target and ctl._task_authorization is grant
            assert target in ctl._dialog._task_status.text()
            send('请使用 bash 执行 cat note.txt，读取并回复 verification_code。不要修改文件。')
            idle()
            conversation = conversation or ctl._convo_id
            assert ctl._convo_id == conversation and ctl._task_authorization is grant
            runs = audit_store.list_runs(conversation)
            run = runs[-1] if runs[-1]['config']['model'] == target else runs[0]
            assert run['config']['model'] == target and run['status'] == 'succeeded'
            assert run['config']['admission']['model'] == target
            assert any(step['tool_name'] == 'bash' for step in run['steps'])
            assert 'ACCEPTANCE-READ-5741' in answer()
            selected.append(target)
        ctl._dialog.new_convo_requested.emit()
        assert ctl._task_authorization is None
        return {'models_used': selected, 'grant_preserved_on_model_switch': True,
                'new_conversation_tools_disabled': True, 'model_identity_matched': True}

    def task_model_settings():
        from ai_desktop.llm.chat_client import _payload
        from ai_desktop.llm.run_worker import RunWorker
        from ai_desktop.services.execution_context import ExecutionSnapshot
        from ai_desktop.services.task_admission import TaskAuthorization, TaskModelSettings, validate_discovery
        from ai_desktop.ui.fluent import ScrollArea
        from ai_desktop.ui.settings_dialog import SettingsDialog

        general()
        values = {'num_ctx': 81920, 'num_predict': 20477, 'temperature': 0.35,
                  'top_p': 0.85, 'top_k': 40, 'repeat_penalty': 1.1}
        settings = SettingsDialog(ctl._settings.current(), parent=ctl._dialog, credentials=object(), model=model)
        settings.settings_applied.connect(ctl._on_settings_applied)
        panel = None
        worker = None
        try:
            settings.show()
            app.processEvents()
            for key, value in values.items():
                settings._widgets[key].setValue(value)
            for route in ['model', 'generation']:
                settings._pivot.setCurrentItem(route)
                wait(lambda: settings._pivot.slideAni.state() == 0, timeout=3)
                app.processEvents()
                if output:
                    preview = Path(output).parent/'previews'/('task-parameters-'+route+'.png')
                    preview.parent.mkdir(parents=True, exist_ok=True)
                    assert settings.grab().save(str(preview))
            settings._save_button.click()
            assert settings.result() == settings.Accepted
            ctl._settings.load()
            snapshot = TaskModelSettings.from_config(ctl._resolve_model_config())
            assert dict(snapshot.options) == values
            panel = TaskDialog(model=model, settings=snapshot, parent=ctl._dialog)
            panel.resize(560, 800)
            panel.show()
            app.processEvents()
            assert '上下文 81920' in panel._profile_summary.text()
            assert '输出 20477' in panel._profile_summary.text()
            scroll = panel.findChild(ScrollArea)
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            app.processEvents()
            if output:
                preview = Path(output).parent/'previews'/'task-parameters-authorization.png'
                preview.parent.mkdir(parents=True, exist_ok=True)
                assert panel.grab().save(str(preview))
            # Synthetic discovery verifies wiring without loading an 81920-token
            # model or accessing credentials. Real HTTP is covered by pytest.
            admission = validate_discovery(config.OLLAMA_BASE_URL, {'version': 'fixture'},
                {'models': [{'name': model, 'digest': 'fixture'}]},
                {'capabilities': ['tools', 'completion'], 'thinking': {'values': [False, True]},
                 'model_info': {'fixture.context_length': 131072}}, model=model, settings=snapshot)
            authorization = TaskAuthorization(execution=ExecutionSnapshot.create(workspace))
            kwargs = authorization.worker_kwargs(admission, config.OLLAMA_BASE_URL,
                agent_id='general_assistant', origin='chat', model=model, settings=snapshot)
            worker = RunWorker([], 'Parameter wiring fixture', agent_id='general_assistant', **kwargs)
            payload = _payload(worker.request, True)
            assert payload['options'] == admission.record()['options'] == values
            assert payload['think'] is False and payload['model'] == model
            return {'saved_and_reloaded': values, 'preview_matches_request_and_admission': True,
                    'request_think': payload['think'], 'discovery': 'synthetic',
                    'model_or_tools_executed': False, 'credential_access': False}
        finally:
            settings.close()
            if panel:
                panel.close()
            if worker:
                worker.release_attachments()
                worker.deleteLater()

    def restart():
        synthetic = not ctl._convo_id
        if synthetic:
            convo = storage.create_conversation('general_assistant', 'restart fixture')
            storage.save_message(convo.id, 'user', 'saved fixture')
            storage.save_message(convo.id, 'assistant', 'saved answer')
            ctl._on_conversation_selected(convo.id)
        convo_id = ctl._convo_id
        user = next(msg for msg in reversed(ctl._messages) if msg.role == 'user')
        request = ChatClient(model=model).create_request([user], '', conversation_id=convo_id,
                                                            agent_id='general_assistant', think=False)
        audit_store.begin_run(RunContext.create(request), user.id)
        ctl._dialog.hide()
        result = subprocess.run(child_command() + ['--resume-probe', str(root),
                                 '--conversation-id', str(convo_id), '--interrupted-id', request.run_id],
                                capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, 'Restart subprocess failed: '+result.stderr[-300:]
        value = json.loads(result.stdout.strip().splitlines()[-1])
        assert value['restart'] == 'passed'
        return {**value, 'synthetic_history_fixture': synthetic}

    try:
        if only == 'tool_settings_inheritance':
            from ai_desktop.diagnostics.tool_inheritance import acceptance as inheritance_acceptance
            case('tool_settings_inheritance', lambda: inheritance_acceptance(ctl, workspace))
        if only == 'conversation_lifecycle':
            from ai_desktop.diagnostics.conversation_lifecycle import acceptance as lifecycle_acceptance
            case('conversation_lifecycle', lambda: lifecycle_acceptance(
                ctl._dialog, lambda predicate: wait(predicate, timeout=3)))
        if only == 'desktop_recovery':
            from ai_desktop.diagnostics.desktop_recovery import acceptance as recovery_acceptance
            case('desktop_recovery', lambda: recovery_acceptance(
                ctl, lambda predicate: wait(predicate, timeout=3)))
        if only == 'selection_hotkey':
            from ai_desktop.diagnostics.selection_hotkey import acceptance as selection_acceptance
            case('selection_hotkey', lambda: selection_acceptance(
                ctl, lambda predicate: wait(predicate, timeout=3)))
        if only == 'answer_layout':
            from ai_desktop.diagnostics.answer_layout import acceptance as layout_acceptance

            preview_dir = Path(output).parent/'previews'/Path(output).stem if output else None
            case('answer_layout', lambda: layout_acceptance(
                ctl._dialog, lambda predicate: wait(predicate, timeout=3), preview_dir))
        if only in PAID_CASES:
            case(only, lambda: live_search(only.removeprefix('search_')))
        if only == 'all_agent_tools':
            case('all_agent_tools', all_agent_tools)
        if only == 'task_model_settings':
            case('task_model_settings', task_model_settings)
        if only == 'settings_task_limits':
            case('settings_task_limits', settings_limits)
        if only == 'settings_tab_navigation':
            case('settings_tab_navigation', settings_tabs)
        if only == 'model_switch_tools':
            case('model_switch_tools', model_switch_tools)
        case('window_layout_and_restore', window_controls)
        case('ordinary_send_and_regenerate', ordinary)
        for mode in ['current', 'new']:
            for action_id in ['translate', 'explain', 'summarize', 'rewrite']:
                case('action_'+mode+'_'+action_id, lambda a=action_id, m=mode: action_case(a, m))
        case('bash_readonly', readonly)
        for decision in ['approve', 'reject', 'hide']:
            case('bash_'+decision, lambda d=decision: write_case(d))
        case('cancel_then_new_conversation', cancel)
        case('restart_and_readonly_history', restart)
    finally:
        ctl.stop()
        if not ctl._stopped:
            wait(lambda: ctl._stopped, timeout=10)
    report['complete'] = True
    report['passed'] = all(item['status'] == 'passed' for item in report['cases'])
    if output:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
                      'real_local_model': report['real_local_model'],
                      'paid_search_used': report['paid_search_used'],
                      'search_http_attempts': report['search_http_attempts']}), flush=True)
    return 0 if report['passed'] else 1

def main(argv=None):
    faulthandler.enable()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model', choices=['qwen3.5:9b-mlx', 'qwen3.8:27b-mlx'], default='qwen3.5:9b-mlx')
    parser.add_argument('--only', choices=CASES, help='Rerun one acceptance case')
    parser.add_argument('--allow-paid-search', action='store_true', help='Permit one live search in one search case')
    parser.add_argument('--search-agent', choices=['general_assistant', 'code_expert', 'translator',
                                                 'summarizer', 'polisher', 'acceptance-researcher'],
                        default='code_expert')
    parser.add_argument('--resume-probe', help=argparse.SUPPRESS)
    parser.add_argument('--tool-inheritance-resume', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--conversation-id', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--interrupted-id', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        validate_paid_case(args.only, args.allow_paid_search)
        isolated_root()
    except ValueError as exc:
        parser.error(str(exc))
    if args.resume_probe:
        resume_probe(args.resume_probe, args.conversation_id, args.interrupted_id)
        return 0
    if args.tool_inheritance_resume:
        from ai_desktop.diagnostics.tool_inheritance import resume_probe as inheritance_resume
        inheritance_resume()
        return 0
    return acceptance(args.output, args.only, args.model, allow_paid_search=args.allow_paid_search,
                      search_agent=args.search_agent)


if __name__ == '__main__':
    sys.exit(main())
