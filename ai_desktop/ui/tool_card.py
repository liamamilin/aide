"""Default Fluent tool feedback and single-use command confirmation controls."""
import json
import math
import time

from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ai_desktop.services.audit_store import RETENTION_SECONDS
from ai_desktop.services.command_confirmation import CommandConfirmation
from ai_desktop.services.web_search import normalized_sources as search_sources
from ai_desktop.services.web_search import safe_source_url
from ai_desktop.ui.fluent import (
    BodyLabel,
    CaptionLabel,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    TransparentPushButton,
)

_ERRORS = {
    'confirmation_denied': '已拒绝执行', 'confirmation_expired': '确认已过期，未执行',
    'confirmation_cancelled': '已停止', 'confirmation_unavailable': '无法显示确认，未执行',
    'cancelled': '已停止', 'timeout': '命令超时', 'active_timeout': '任务超时',
    'output_limit': '输出达到上限，已停止', 'unfinished_children': '已清理残留子进程',
    'workspace_changed': '工作区已变化，未执行', 'workspace_required': '未选择工作区',
    'invalid_command': '命令无效，未执行', 'start_failed': '启动失败', 'io_failed': '读取输出失败',
}

_SEARCH_ERRORS = {
    'cancelled': '已停止', 'timeout': '搜索超时', 'active_limit': '任务超时',
    'authentication': '密钥或权限无效', 'quota': '账户额度不足', 'rate_limit': '请求过多',
    'redirect': '已停止重定向', 'network': '网络连接失败', 'http': '搜索服务错误',
    'protocol': '响应格式错误', 'output_limit': '响应达到上限',
    'missing_credentials': '未配置密钥', 'credentials_unavailable': '无法读取密钥',
    'invalid_request': '搜索参数无效',
}


def open_source_url(url):
    if safe_source_url(url):
        return QDesktopServices.openUrl(QUrl(url))
    return False


def plain_label(cls, text, parent):
    label = cls(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return label


class ToolCard(SimpleCardWidget):
    confirmation_decided = pyqtSignal(object, bool)
    stop_requested = pyqtSignal(str)

    def __init__(self, event, workspace='', parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self.identity = (event.run_id, event.conversation_id, event.step_id, event.request_id, event.local_call_id)
        self.workspace = workspace if event.tool_name == 'bash' else ''
        self.tool_name = event.tool_name
        self.clock = clock
        self.confirmation = None
        self.terminal = False
        self.pending = False
        self._submitted = False
        self.payload_expires_at = None
        self._payload_purged = False
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        try:
            args = json.loads(event.arguments_json)
        except (ValueError, TypeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        self.command = args.get('command', '')
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(StrongBodyLabel('命令执行' if event.tool_name == 'bash' else '联网搜索', self))
        header.addStretch()
        self.status = plain_label(CaptionLabel, '检查命令' if event.tool_name == 'bash' else '正在搜索', self)
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.status.setAlignment(Qt.AlignRight)
        header.addWidget(self.status)
        layout.addLayout(header)
        self.directory = plain_label(CaptionLabel, f'工作区：{self.workspace}' if self.workspace else '', self)
        self.directory.setVisible(bool(self.workspace))
        layout.addWidget(self.directory)
        self.command_view = PlainTextEdit(self)
        self.command_view.setReadOnly(True)
        self.command_view.setPlainText(self.command or str(args.get('query', '')))
        self.command_view.setAccessibleName('完整命令或搜索参数')
        self.command_view.setMinimumWidth(0)
        self.command_view.setFixedHeight(88)
        layout.addWidget(self.command_view)
        self.reason = plain_label(BodyLabel, '', self)
        self.reason.hide()
        layout.addWidget(self.reason)
        self.countdown = plain_label(CaptionLabel, '', self)
        self.countdown.hide()
        layout.addWidget(self.countdown)
        self.details_button = TransparentPushButton('查看输出', self)
        self.details_button.setAccessibleName('展开或收起工具输出')
        self.details_button.hide()
        self.details_button.clicked.connect(self._toggle_details)
        layout.addWidget(self.details_button, alignment=Qt.AlignLeft)
        self.details = QWidget(self)
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.summary = plain_label(CaptionLabel, '', self.details)
        detail_layout.addWidget(self.summary)
        self.output_view = PlainTextEdit(self.details)
        self.output_view.setReadOnly(True)
        self.output_view.setAccessibleName('工具输出摘录')
        self.output_view.setFixedHeight(144)
        detail_layout.addWidget(self.output_view)
        self.source_layout = QVBoxLayout()
        detail_layout.addLayout(self.source_layout)
        self.source_buttons = []
        self.details.hide()
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.approve = PrimaryPushButton('执行', self)
        self.reject = PushButton('拒绝', self)
        self.stop = PushButton('停止', self)
        for button, name in ((self.approve, '执行此命令'), (self.reject, '拒绝此命令'), (self.stop, '停止当前任务')):
            button.setAccessibleName(name)
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.approve.hide()
        self.reject.hide()
        self.approve.clicked.connect(lambda: self._decide(True))
        self.reject.clicked.connect(lambda: self._decide(False))
        self.stop.clicked.connect(self._stop)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_expiry)

    def matches(self, event):
        return self.identity == (event.run_id, event.conversation_id, event.step_id,
                                 event.request_id, event.local_call_id)

    def bind_confirmation(self, request):
        if (not isinstance(request, CommandConfirmation) or self.terminal or self.confirmation is not None
                or self.identity != (request.run_id, request.conversation_id, request.step_id,
                                     request.request_id, request.tool_call_id)
                or self.command != request.command or self.workspace != request.workspace):
            return False
        self.confirmation = request
        self.pending = True
        self.status.setText('等待确认')
        self.reason.setText(f'{request.reason}\n命令超时：{request.timeout} 秒')
        self.reason.show()
        self.countdown.show()
        self.approve.show()
        self.reject.show()
        self.timer.start()
        self.refresh_expiry()
        return True

    def refresh_expiry(self):
        if not self.pending or self.confirmation is None:
            return
        remaining = math.ceil(self.confirmation.expires_at - self.clock())
        if remaining <= 0:
            self.pending = False
            self._disable_decisions()
            self.status.setText('确认已过期，未执行')
            self.countdown.setText('可等待助手继续处理，或停止任务。')
            self.timer.stop()
        else:
            self.countdown.setText(f'{remaining // 60}:{remaining % 60:02d} 后过期 · 等待不占任务活动时长')

    def _disable_decisions(self):
        self.approve.setEnabled(False)
        self.reject.setEnabled(False)

    def _decide(self, approved):
        self.refresh_expiry()
        if self.terminal or not self.pending or self._submitted:
            return
        self._submitted = True
        self._disable_decisions()
        self.status.setText('正在提交确认' if approved else '正在拒绝')
        self.confirmation_decided.emit(self.confirmation, approved)

    def response_acknowledged(self, accepted, approved):
        self.pending = False
        self.timer.stop()
        self.countdown.hide()
        self.status.setText(('已批准，准备执行' if approved else '已拒绝执行') if accepted else '确认已失效，未执行')
        self._disable_decisions()

    def _stop(self):
        if self.terminal:
            return
        self.invalidate()
        self.stop_requested.emit(self.identity[0])

    def invalidate(self, status='已停止'):
        if self.terminal:
            return
        self.terminal = True
        self.payload_expires_at = time.time() + RETENTION_SECONDS
        self.pending = False
        self.timer.stop()
        self.countdown.hide()
        self._disable_decisions()
        self.stop.setEnabled(False)
        self.approve.hide()
        self.reject.hide()
        self.stop.hide()
        self.status.setText(status)

    def update_event(self, event):
        if self.terminal or not self.matches(event):
            return
        if event.output is None:
            if event.status == 'executing':
                self.pending = False
                self.timer.stop()
                self.countdown.hide()
                self._disable_decisions()
                self.status.setText('执行中')
            elif event.status == 'waiting_confirmation':
                self.status.setText('等待确认')
            elif event.status == 'searching':
                self.status.setText('正在搜索')
            return
        self.invalidate('执行失败' if event.output.error else '已完成')
        try:
            record = json.loads(event.output.text)
        except (ValueError, TypeError):
            record = None
        if self.tool_name == 'web_search' and isinstance(record, dict) and 'sources' in record:
            sources = search_sources(record)
            error = record.get('error_type', '')
            returned = record.get('returned_sources')
            count = (f'保留 {len(sources)} / {returned} 个来源'
                     if type(returned) is int and len(sources) < returned <= 99 else
                     f'找到 {len(sources)} 个来源' if sources else '未找到来源')
            self.status.setText(_SEARCH_ERRORS.get(error, '搜索失败') if event.output.error else
                                count)
            provider = {'exa': 'Exa', 'parallel': 'Parallel'}.get(record.get('provider'), '搜索服务')
            self.summary.setText(f'{provider} · {record.get("duration", 0)} 秒'
                                 + (' · 来源或摘录已按上下文预算裁剪' if record.get('truncated') else ''))
            self.output_view.setPlainText(record.get('error', '') or '\n\n'.join(
                f'[{s["source_id"]}] {s["title"]}\n{s["url"]}\n{s.get("excerpt", "")}' for s in sources)
                or '没有找到相关来源，可以调整搜索词。')
            for source in sources:
                title = plain_label(BodyLabel, f'[{source["source_id"]}] {source["title"]}', self.details)
                self.source_layout.addWidget(title)
                button = TransparentPushButton('打开来源', self.details)
                button.setAccessibleName(f'打开 {source["source_id"]} 的来源网页')
                button.setToolTip(source['url'])
                button.setAutoDefault(False)
                button.clicked.connect(lambda checked=False, url=source['url']: self._open_source(url))
                self.source_layout.addWidget(button, alignment=Qt.AlignLeft)
                self.source_buttons.append(button)
            self.details_button.setText('查看来源' if sources else '查看详情')
        elif isinstance(record, dict) and 'error_type' in record:
            error = record.get('error_type', '')
            if error:
                self.status.setText(_ERRORS.get(error, '执行失败'))
            code = record.get('exit_code')
            duration = record.get('duration', 0)
            self.summary.setText(f'退出码：{code if code is not None else "未产生"} · {duration} 秒'
                                 + (' · 输出已截断' if record.get('truncated') else ''))
            self.output_view.setPlainText(f'stdout\n{record.get("stdout", "")}\n\nstderr\n{record.get("stderr", "")}')
        else:
            self.summary.setText('工具结果摘录')
            self.output_view.setPlainText(event.output.text)
        self.details_button.show()

    def _open_source(self, url):
        if not self.purge_expired_payload():
            open_source_url(url)

    def purge_expired_payload(self):
        if self._payload_purged:
            return True
        if not self.terminal or self.payload_expires_at is None or time.time() < self.payload_expires_at:
            return False
        self._payload_purged = True
        self.command = ''
        self.confirmation = None
        self.command_view.clear()
        self.output_view.clear()
        self.reason.setText('执行详情已过期')
        self.reason.show()
        self.details_button.hide()
        self.details.hide()
        for button in self.source_buttons:
            button.setEnabled(False)
        self.source_buttons.clear()
        while self.source_layout.count():
            item = self.source_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        return True

    def _toggle_details(self):
        expanded = not self.details.isHidden()
        self.details.setVisible(not expanded)
        noun = '来源' if self.tool_name == 'web_search' and self.source_buttons else '输出'
        self.details_button.setText(f'查看{noun}' if expanded else f'收起{noun}')


def probe_tool_card(dialog):
    """Frozen native UI smoke: display/reject a synthetic card, execute nothing."""
    from dataclasses import replace

    from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput

    event = RunEvent('ui-smoke', 0, 'step', 'request', 1, RunEventKind.TOOL_STARTED, 'call',
                     tool_name='bash', arguments_json=json.dumps({'command': 'printf smoke'}))
    dialog.begin_tool_run(event.run_id)
    dialog.show_tool_event(event, '/tmp/aide-ui-smoke')
    card = dialog._tool_cards[(event.run_id, event.local_call_id)]
    request = CommandConfirmation('smoke', event.run_id, event.step_id, event.request_id, 0, 'call',
                                  card.command, card.workspace, 'confirm_all', 30,
                                  '打包界面自检，不执行命令', 'smoke', time.monotonic()+600)
    if not dialog.show_command_confirmation(request):
        raise RuntimeError('Command card did not accept its synthetic identity')
    card.reject.click()
    output = ToolOutput(json.dumps({'exit_code': None, 'stdout': '', 'stderr': '', 'duration': 0,
                                   'truncated': False, 'error_type': 'confirmation_denied'}), True)
    dialog.show_tool_event(replace(event, kind=RunEventKind.TOOL_FINISHED, output=output))
    card.details_button.click()
    dialog.finish_tool_run()
    if card.timer.isActive() or card.approve.isEnabled() or card.details.isHidden():
        raise RuntimeError('Command card did not finalize')


def probe_search_card(dialog):
    """Frozen UI/source-resolution smoke, with no network or credential access."""
    from dataclasses import replace

    from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput

    dialog.begin_assistant_stream()
    dialog.begin_tool_run('search-ui-smoke')
    event = RunEvent('search-ui-smoke', 0, 'step', 'request', 1, RunEventKind.TOOL_STARTED, 'search',
                     tool_name='web_search', arguments_json='{"query":"Python documentation"}')
    dialog.show_tool_event(event)
    source = {'source_id': 'S1', 'title': 'Python documentation', 'url': 'https://docs.python.org/',
              'excerpt': 'Synthetic source for isolated UI verification.'}
    output = ToolOutput(json.dumps({'provider': 'exa', 'sources': [source], 'duration': 0,
                                   'error_type': '', 'error': '', 'truncated': False}))
    dialog.show_tool_event(replace(event, kind=RunEventKind.TOOL_FINISHED, output=output))
    card = dialog._tool_cards[(event.run_id, event.local_call_id)]
    card.details_button.click()
    label = dialog._stream_bubble
    dialog.finalize_assistant_stream('隔离来源自检 [S1] · 未核验编号 [S2]', True)
    dialog.finish_tool_run()
    if (not card.terminal or len(card.source_buttons) != 1 or card.details.isHidden()
            or label._search_sources.get('S1', {}).get('url') != source['url'] or 'source://S1' not in label.text()
            or 'source://S2' in label.text()):
        raise RuntimeError('Search card or verified source resolution did not finalize')
