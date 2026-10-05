"""Read-only Fluent run history. Opening history never creates a worker."""
import json
import time
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput
from ai_desktop.services.audit_store import list_runs
from ai_desktop.ui.fluent import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentDialog,
    PlainTextEdit,
    ScrollArea,
    SimpleCardWidget,
    TransparentPushButton,
    dialog_title,
)
from ai_desktop.ui.tool_card import ToolCard, plain_label

_STATUS = {'running': '运行中', 'cancelling': '正在停止', 'succeeded': '已完成', 'failed': '失败',
           'cancelled': '已停止', 'limited': '达到上限', 'interrupted': '已中断',
           'waiting_confirmation': '等待确认', 'executing': '执行中', 'searching': '搜索中'}


class RunHistoryDialog(FluentDialog):
    def __init__(self, conversation_id, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setMinimumSize(360, 360)
        self.resize(560, 680)
        self.conversation_id = conversation_id
        root = QVBoxLayout(self)
        root.addWidget(dialog_title(self, '运行记录'))
        root.addWidget(plain_label(CaptionLabel, '只读记录 · 执行详情保留 30 天 · 不会重新运行工具', self))
        self.selector = ComboBox(self)
        root.addWidget(self.selector)
        refresh = TransparentPushButton('刷新记录', self)
        refresh.clicked.connect(self.reload)
        root.addWidget(refresh, alignment=Qt.AlignLeft)
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container = QWidget()
        self.content = QVBoxLayout(self.container)
        self.content.addStretch()
        scroll.setWidget(self.container)
        root.addWidget(scroll)
        self.cards = []
        self.selector.currentIndexChanged.connect(self._show_run)
        self.reload()

    def reload(self):
        previous = self.selector.currentData()
        self.runs = list_runs(self.conversation_id)
        self.selector.blockSignals(True)
        self.selector.clear()
        for run in self.runs:
            date = datetime.fromtimestamp(run['started_at']).strftime('%m-%d %H:%M:%S')
            origin = '快捷动作' if run['origin'] == 'action' else '对话'
            self.selector.addItem(f'{date} · {origin} · {_STATUS.get(run["status"], "未知状态")}', run['run_id'])
        index = self.selector.findData(previous)
        self.selector.setCurrentIndex(index if index >= 0 else 0)
        self.selector.blockSignals(False)
        self._show_run(self.selector.currentIndex())

    def _show_run(self, index):
        self.cards = []
        while self.content.count() > 1:
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not 0 <= index < len(self.runs):
            self.content.insertWidget(0, plain_label(BodyLabel, '这个对话还没有运行记录。', self.container))
            return
        run = self.runs[index]
        config = run['config']
        model = config.get('model', '')
        status = _STATUS.get(run['status'], '未知状态')
        self.content.insertWidget(0, plain_label(BodyLabel, f'{model} · {status}', self.container))
        self.content.insertWidget(1, plain_label(CaptionLabel, f'运行 ID：{run["run_id"]}', self.container))
        execution = config.get('execution') or {}
        for step in run['steps']:
            if step['payload_expires_at'] is not None and step['payload_expires_at'] <= time.time():
                step['payload'] = None
            payload = step['payload']
            if step['kind'] == 'tool' and payload is not None:
                event = RunEvent(run['run_id'], run['conversation_id'], step['step_id'], step['request_id'],
                                 step['step_index'], RunEventKind.TOOL_STARTED, step['tool_call_id'],
                                 tool_name=step['tool_name'], arguments_json=payload.get('arguments_json', '{}'))
                card = ToolCard(event, execution.get('workspace', ''), self.container)
                output = payload.get('output')
                if isinstance(output, str):
                    from dataclasses import replace
                    card.update_event(replace(event, kind=RunEventKind.TOOL_FINISHED,
                                              output=ToolOutput(output, bool(step['metadata'].get('error')))))
                else:
                    card.invalidate(_STATUS.get(step['status'], '已中断'))
                self.cards.append(card)
            else:
                card = SimpleCardWidget(self.container)
                layout = QVBoxLayout(card)
                name = step['tool_name'] or '模型请求'
                layout.addWidget(plain_label(BodyLabel, f'{name} · {_STATUS.get(step["status"], "未知状态")}', card))
                if payload is None:
                    layout.addWidget(plain_label(CaptionLabel, '执行详情已过期', card))
                else:
                    button = TransparentPushButton('查看内容', card)
                    editor = PlainTextEdit(card)
                    editor.setReadOnly(True)
                    editor.setAccessibleName('已脱敏的模型步骤内容')
                    editor.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
                    editor.setFixedHeight(220)
                    editor.hide()
                    button.clicked.connect(lambda checked=False, widget=editor: widget.setVisible(widget.isHidden()))
                    layout.addWidget(button, alignment=Qt.AlignLeft)
                    layout.addWidget(editor)
            self.content.insertWidget(self.content.count()-1, card)


def probe_run_audit(parent):
    """Synthetic isolated storage/UI lifecycle; no worker, commands or search."""
    from dataclasses import replace

    from ai_desktop.llm.chat_client import ChatClient
    from ai_desktop.llm.run_types import RunContext, ToolSpec
    from ai_desktop.services import audit_store
    from ai_desktop.utils import storage

    convo = storage.create_conversation('general_assistant')
    user = storage.save_message(convo.id, 'user', 'Audit smoke fixture')
    request = ChatClient().create_request([user], '', conversation_id=convo.id, agent_id='general_assistant')
    spec = ToolSpec.create('web_search', lambda *_: ToolOutput('unused'))
    audit_store.begin_run(RunContext.create(request, tools=(spec,), tools_admitted=True), user.id)
    event = RunEvent(request.run_id, convo.id, request.step_id, request.request_id, 1,
                     RunEventKind.MODEL_STARTED, payload_json='{"input":[{"content":"API_KEY=audit-test-value"}]}')
    audit_store.observe(event)
    audit_store.observe(replace(event, seq=2, kind=RunEventKind.MODEL_FINISHED, status='succeeded',
                                payload_json='{"output":{"content":"","done_reason":"stop"}}'))
    start = replace(event, seq=3, kind=RunEventKind.TOOL_STARTED, local_call_id='call', tool_name='web_search',
                    arguments_json='{"query":"Python docs"}', payload_json='')
    audit_store.observe(start)
    output = ToolOutput(json.dumps({'provider': 'exa', 'error_type': '', 'sources': [
        {'source_id': 'S1', 'title': 'Python docs', 'url': 'https://docs.python.org/',
         'excerpt': 'Synthetic source'}]}))
    audit_store.observe(replace(start, seq=4, kind=RunEventKind.TOOL_FINISHED, status='succeeded', output=output))
    audit_store.observe(replace(event, seq=5, kind=RunEventKind.FINISHED, status='succeeded', payload_json=''))
    message = storage.save_message(convo.id, 'assistant', 'Docs [S1]')
    generation = storage.save_generation(user.id, request.request_id, config_snapshot={'run_id': request.run_id},
                                         answer=message.content, status='succeeded',
                                         assistant_message_id=message.id, active=True)
    if (not audit_store.sources_for_message(generation_id=generation.id)
            or 'audit-test-value' in audit_store.export_markdown(convo.id)):
        raise RuntimeError('Audit source linkage or redaction failed')
    dialog = RunHistoryDialog(convo.id, parent)
    dialog.show()
    if len(dialog.cards) != 1 or not dialog.cards[0].terminal or dialog.cards[0].stop.isEnabled():
        raise RuntimeError('Audit history is not read-only')
    with storage._conn() as db:
        db.execute('UPDATE agent_steps SET payload_expires_at=? WHERE run_id=?', (time.time()-1, request.run_id))
    dialog.reload()
    if (audit_store.sources_for_message(generation_id=generation.id)
            or '执行详情已过期' not in audit_store.export_markdown(convo.id)):
        raise RuntimeError('Audit expiry failed')
    dialog.close()
    storage.delete_conversation(convo.id)
    if audit_store.list_runs(convo.id):
        raise RuntimeError('Audit conversation cascade failed')
