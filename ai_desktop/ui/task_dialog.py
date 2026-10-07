"""Default Fluent controls for explicit tool authorization in one conversation."""
from dataclasses import replace

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from ai_desktop import config
from ai_desktop.llm.run_types import RunLimits
from ai_desktop.llm.task_checks import TaskChecks
from ai_desktop.services.execution_context import (
    BashPolicy,
    ExecutionSnapshot,
    load_execution_preferences,
    load_workspace_policy,
    save_execution_preferences,
)
from ai_desktop.services.task_admission import TASK_MODEL, TaskAuthorization, TaskModelSettings
from ai_desktop.services.web_search import SearchError, SearchSettings
from ai_desktop.ui.fluent import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentDialog,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    dialog_title,
)


class TaskDialog(FluentDialog):
    def __init__(self, current=None, *, workspace_hint='', model=TASK_MODEL, settings=None,
                 agent_id='general_assistant', agent_name='', parent=None):
        super().__init__(parent)
        if current is not None and current.agent_id != agent_id:
            current = None
        self.authorization = current
        self.admission = None
        self._checking = False
        self._checks = TaskChecks(self)
        self._checks.completed.connect(self._on_checked)
        self._candidate = None
        self._base_url = config.OLLAMA_BASE_URL
        self._model = model
        self._agent_id = agent_id
        self._model_settings = settings or TaskModelSettings.from_config()
        preferences = load_execution_preferences()
        self._path = preferences['execution_path']
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(dialog_title(self, '工具授权'))
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        intro = BodyLabel(f'当前角色：{agent_name or agent_id}。授权仅在当前角色与对话的后续轮次有效。'
                          '新建、切换对话或切换角色后关闭，需重新启用。'
                          '工具模型跟随顶栏选择。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(StrongBodyLabel('本地命令'))
        self._bash = CheckBox('允许 Bash 工具')
        self._bash.setChecked(bool(current and current.execution))
        layout.addWidget(self._bash)
        row = QHBoxLayout()
        self._workspace = LineEdit()
        self._workspace.setAccessibleName('工具工作目录')
        self._workspace.setPlaceholderText('选择工作目录')
        self._workspace.setText(current.execution.workspace if current and current.execution else
                                workspace_hint or preferences['execution_workspace'])
        row.addWidget(self._workspace, 1)
        self._browse = PushButton('选择…')
        self._browse.clicked.connect(self._choose_directory)
        row.addWidget(self._browse)
        layout.addLayout(row)
        self._policy = ComboBox()
        self._policy.setAccessibleName('Bash 命令策略')
        self._policy.addItem('允许列表内只读命令自动，其余确认', BashPolicy.READONLY_AUTO.value)
        self._policy.addItem('每条命令都确认', BashPolicy.CONFIRM_ALL.value)
        policy = (current.execution.policy if current and current.execution else
                  load_workspace_policy(self._workspace.text()))
        self._policy.setCurrentIndex(self._policy.findData(policy.value))
        self._workspace.editingFinished.connect(self._load_policy)
        layout.addWidget(self._policy)
        boundary = CaptionLabel('复杂命令经确认后可读写其他位置；工作目录不是系统沙箱。')
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addWidget(StrongBodyLabel('联网搜索'))
        self._search = CheckBox('允许联网搜索（按服务计费）')
        self._search.setChecked(bool(current and current.search))
        layout.addWidget(self._search)
        self._provider = ComboBox()
        self._provider.setAccessibleName('任务搜索服务')
        self._provider.addItem('Parallel', 'parallel')
        self._provider.addItem('Exa', 'exa')
        provider = current.search.provider if current and current.search else config.SEARCH_PROVIDER
        self._provider.setCurrentIndex(self._provider.findData(provider))
        layout.addWidget(self._provider)
        hint = CaptionLabel('密钥在设置 → 联网搜索中管理。搜索内容会发送给所选服务。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(StrongBodyLabel('本次工具任务配置'))
        limits = RunLimits.from_config()
        self._profile_summary = BodyLabel(self._model_settings.summary(self._model) + '\n'
                            '每次发送前检查所选模型的工具与思考能力。\n'
                            f'最多 {limits.max_model_rounds} 轮模型、{limits.max_tool_calls} 次工具、'
                            f'其中 {limits.max_search_calls} 次搜索；活动时长 5 分钟。\n'
                            '次数限制可在设置 → 工具执行中调整。')
        self._profile_summary.setWordWrap(True)
        layout.addWidget(self._profile_summary)
        self._status = CaptionLabel('启用前只检查本机服务，不调用模型或搜索。')
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(16, 8, 16, 16)
        self._disable = PushButton('关闭工具')
        self._disable.setVisible(current is not None)
        self._disable.clicked.connect(self._turn_off)
        buttons.addWidget(self._disable)
        buttons.addStretch()
        cancel = PushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._enable = PrimaryPushButton('启用工具')
        self._enable.clicked.connect(self._start_check)
        buttons.addWidget(self._enable)
        root.addLayout(buttons)
        self._bash.stateChanged.connect(self._update_controls)
        self._search.stateChanged.connect(self._update_controls)
        self._update_controls()
        self.setMinimumSize(360, 480)
        self.resize(520, 680)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

    def _load_policy(self):
        self._policy.setCurrentIndex(self._policy.findData(load_workspace_policy(self._workspace.text()).value))

    def _choose_directory(self):
        selected = QFileDialog.getExistingDirectory(self, '工具工作目录', self._workspace.text())
        if selected:
            self._workspace.setText(selected)
            self._load_policy()

    def _update_controls(self):
        for widget in (self._workspace, self._browse, self._policy):
            widget.setEnabled(self._bash.isChecked() and not self._checking)
        self._provider.setEnabled(self._search.isChecked() and not self._checking)
        self._bash.setEnabled(not self._checking)
        self._search.setEnabled(not self._checking)
        self._enable.setEnabled(not self._checking and (self._bash.isChecked() or self._search.isChecked()))

    def _start_check(self):
        try:
            execution = (ExecutionSnapshot.create(self._workspace.text(), self._policy.currentData(),
                                                   search_path=self._path.split(':'))
                         if self._bash.isChecked() else None)
            search = (replace(SearchSettings.from_config(), provider=self._provider.currentData())
                      if self._search.isChecked() else None)
            self._candidate = TaskAuthorization(execution, search, agent_id=self._agent_id)
        except (OSError, ValueError, SearchError) as exc:
            self._status.setText(str(exc))
            return
        self._checking = True
        self._status.setText('正在验证本机服务与模型…')
        self._update_controls()
        self._checks.check(self._base_url, self._model, settings=self._model_settings)

    def _on_checked(self, sequence, admission, error):
        self._checking = False
        self._update_controls()
        if error:
            self._status.setText(error)
            return
        try:
            self._candidate.worker_kwargs(admission, config.OLLAMA_BASE_URL,
                                          agent_id=self._agent_id, origin='chat', model=self._model,
                                          settings=self._model_settings)
            if self._candidate.execution:
                snapshot = self._candidate.execution
                save_execution_preferences({'execution_workspace': snapshot.workspace,
                                            'bash_policy': snapshot.policy.value,
                                            'execution_path': ':'.join(snapshot.search_path)})
        except (OSError, ValueError) as exc:
            self._status.setText(str(exc))
            return
        self.authorization, self.admission = self._candidate, admission
        self.accept()

    def _turn_off(self):
        self.authorization = None
        self.accept()

    def done(self, result):
        self._checks.cancel()
        super().done(result)


def probe_task_entry(chat):
    """Synthetic native/frozen UI checks; no service checks, keys, or executors."""
    import json

    from ai_desktop.llm.events import EventKind, StreamEvent
    from ai_desktop.llm.run_types import RunEvent, RunEventKind
    from ai_desktop.services.task_admission import TASK_DIGEST, TASK_SERVICE_VERSION, validate_discovery
    from ai_desktop.ui.task_step_card import TaskStepCard

    fixture = TaskModelSettings.from_config()
    fixture = replace(fixture, options=tuple({**dict(fixture.options), 'num_ctx': 8192, 'num_predict': 512}.items()))
    admission = validate_discovery('http://localhost:11434', {'version': TASK_SERVICE_VERSION},
                                   {'models': [{'name': TASK_MODEL, 'digest': TASK_DIGEST}]},
                                   {'capabilities': ['tools', 'completion'], 'thinking': {'values': [False, True]}},
                                   settings=fixture)
    assert admission.valid('http://localhost:11434')
    setup = TaskDialog(parent=chat, settings=fixture)
    assert not setup._enable.isEnabled() and setup.authorization is None
    setup.show()
    setup.reject()
    setup.deleteLater()
    authorization = TaskAuthorization(search=SearchSettings(provider='exa'))
    chat.set_task_authorization(authorization)
    chat.begin_tool_run('task-smoke')
    event = RunEvent('task-smoke', 0, 'step', 'request', 1, RunEventKind.MODEL_STARTED)
    chat.show_task_model_event(event)
    chat.append_task_stream(StreamEvent('request', EventKind.CONTENT, '合成步骤文本', run_id='task-smoke',
                                        step_id='step', seq=2))
    chat._flush_stream_buffer()
    card = chat._stream_container
    assert isinstance(card, TaskStepCard) and '合成步骤文本' in card.body.text()
    chat.show_task_model_event(replace(event, seq=3, kind=RunEventKind.MODEL_FINISHED, status='succeeded',
                                     payload_json=json.dumps({'output': {'content': '合成步骤文本', 'thinking': ''}})))
    chat.finalize_assistant_stream('合成步骤文本', True)
    assert card.final_answer and card.header.text() == '回答'
    chat.set_regenerate_state(True, [{'id': 0, 'answer': '合成步骤文本', 'active': True, 'task': True}])
    assert card.regenerate.text() == '重新执行'
    chat.finish_tool_run()
    chat.set_task_authorization(None)
