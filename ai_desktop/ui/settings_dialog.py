"""
设置面板
"""
import json
import os
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from ai_desktop.llm.thinking import ThinkMode, ThinkSetting
from ai_desktop.services.execution_context import DEFAULT_PATH, BashPolicy, ExecutionSnapshot, load_workspace_policy
from ai_desktop.services.qt_search import SearchJob
from ai_desktop.services.search_credentials import CredentialError, SearchCredentials
from ai_desktop.services.web_search import MAX_SEARCH_RESULTS, SearchError, SearchSettings, build_request
from ai_desktop.ui.fluent import BodyLabel as QLabel
from ai_desktop.ui.fluent import (
    CaptionLabel,
    Pivot,
    PrimaryPushButton,
    SimpleCardWidget,
    SubtitleLabel,
    dialog_title,
)
from ai_desktop.ui.fluent import CheckBox as QCheckBox
from ai_desktop.ui.fluent import ComboBox as QComboBox
from ai_desktop.ui.fluent import DoubleSpinBox as QDoubleSpinBox
from ai_desktop.ui.fluent import FluentDialog as QDialog
from ai_desktop.ui.fluent import LineEdit as QLineEdit
from ai_desktop.ui.fluent import MessageBox as QMessageBox
from ai_desktop.ui.fluent import PushButton as QPushButton
from ai_desktop.ui.fluent import ScrollArea as QScrollArea
from ai_desktop.ui.fluent import SpinBox as QSpinBox
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.ui.thinking_selector import ThinkingSelector
from ai_desktop.utils.paths import resource_path


def _discover_pets() -> list[tuple[str, str, str]]:
    """Return list of (display_label, source, name) for all available pets."""
    pets: list[tuple[str, str, str]] = []

    built_in_dir = resource_path("ai_desktop", "pets")
    if os.path.isdir(built_in_dir):
        for entry in sorted(os.listdir(built_in_dir)):
            manifest = os.path.join(built_in_dir, entry, "pet.json")
            if os.path.isfile(manifest):
                label = _pet_display_name(manifest, entry)
                pets.append((f"{label} (内置)", "built-in", entry))

    petdex_dir = Path.home() / ".petdex" / "pets"
    if petdex_dir.is_dir():
        for entry in sorted(petdex_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = entry / "pet.json"
            if manifest.is_file():
                label = _pet_display_name(str(manifest), entry.name)
                pets.append((f"{label} (Petdex)", "petdex", entry.name))

    return pets


def _pet_display_name(manifest_path: str, fallback: str) -> str:
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("displayName") or data.get("name") or fallback
    except (OSError, json.JSONDecodeError, KeyError):
        return fallback


class SettingsDialog(FramelessDragMixin, QDialog):
    settings_applied = pyqtSignal(dict)

    FIELDS = [
        ("base_url", "Ollama 服务地址", str, "http://localhost:11434"),
        ("think", "模型思考推理", ThinkSetting, ThinkSetting(ThinkMode.ON)),
        ("timeout", "响应超时（秒）", int, 120),
        ("num_ctx", "上下文窗口（token）", int, 8192),
        ("max_rounds", "保留对话轮次", int, 10),
        ("num_predict", "最大输出（含思考 token）", int, 20480),
        ("temperature", "Temperature", float, 0.7),
        ("top_p", "Top P", float, 0.9),
        ("top_k", "Top K", int, 40),
        ("repeat_penalty", "Repeat Penalty", float, 1.1),
        ("search_provider", "默认搜索服务", str, "parallel"),
        ("search_max_results", "最多来源数", int, 5),
        ("search_timeout", "搜索超时（秒）", int, 30),
        ("search_parallel_mode", "Parallel 搜索模式", str, "basic"),
        ("desktop_pet", "显示桌面宠物入口", bool, True),
        ("pet_source", "宠物形象", str, "built-in"),
        ("pet_size", "宠物尺寸", str, "medium"),
        ("pet_reduce_motion", "减少宠物动画", bool, False),
        ("quick_actions", "选中文字后显示快捷动作", bool, True),
        ("hotkey", "选区提问快捷键", str, "<cmd>+<ctrl>+l"),
        ("execution_workspace", "任务工作区", str, ""),
        ("bash_policy", "命令确认策略", str, "readonly_auto"),
        ("execution_path", "命令搜索路径 PATH", str, ":".join(DEFAULT_PATH)),
        ("task_tools_enabled", "允许通用助手启用工具", bool, True),
        ("task_max_tool_calls", "每次任务最多工具调用", int, 16),
        ("task_max_search_calls", "其中最多联网搜索", int, 6),
        ("task_max_model_rounds", "每次任务最多模型轮次", int, 8),
    ]
    GROUPS = (
        ("model", "模型与对话", "连接本地模型", "角色和 Action 的专用模型配置仍在原有入口管理。", 0, 5),
        ("generation", "生成参数", "全局生成默认值", "专用配置可覆盖这些值。通常保持默认即可。", 5, 10),
        ("search", "联网搜索", "搜索服务", "每次搜索可保留 1–99 个来源；实际数量取决于服务返回和上下文预算。", 10, 14),
        ("execution", "工具执行", "工作区与任务限制", "工作区只指定起始目录；确认策略按工作区保存。", 20, 27),
        ("desktop", "桌面偏好", "宠物与快捷动作", "选择入口形象、动画偏好和选区操作方式。", 14, 20),
    )
    INT_RANGES = {
        "timeout": (1, 600), "num_ctx": (256, 999999), "num_predict": (1, 999999),
        "top_k": (0, 200), "max_rounds": (1, 100), "search_max_results": (1, MAX_SEARCH_RESULTS),
        "search_timeout": (5, 120), "task_max_tool_calls": (1, 99), "task_max_search_calls": (1, 99),
        "task_max_model_rounds": (1, 99),
    }
    FLOAT_RANGES = {"temperature": (0, 2), "top_p": (0, 1), "repeat_penalty": (0, 2)}
    CHOICES = {
        "bash_policy": (("已验证的只读命令自动执行", "readonly_auto"), ("所有命令均需确认", "confirm_all")),
        "pet_size": (("小", "small"), ("中", "medium"), ("大", "large")),
        "search_provider": (("Parallel", "parallel"), ("Exa", "exa")),
        "search_parallel_mode": (("Basic · 基础", "basic"), ("Fast · 快速", "fast"),
                                 ("Turbo · 极速", "turbo"), ("Advanced · 深入", "advanced")),
    }

    def __init__(self, current: dict, parent=None, *, credentials=None, model="", model_version=""):
        super().__init__(parent)
        self._setup_drag(40)
        self._current = current
        self._model = model
        self._model_version = model_version
        self._credentials = credentials if credentials is not None else SearchCredentials()
        self._search_job = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(640, 540)
        self.resize(720, 640)
        self._setup_ui()
        self._load()
        self._widgets["base_url"].textChanged.connect(self._thinking_model_changed)
        self._thinking_model_changed()
        self._policy_workspace = self._widgets["execution_workspace"].text().strip()
        self._widgets["bash_policy"].setEnabled(bool(self._policy_workspace))
        self._widgets["execution_path"].setEnabled(bool(self._policy_workspace))
        self._widgets["execution_workspace"].editingFinished.connect(self._workspace_changed)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(dialog_title(self, "设置"))
        body = QVBoxLayout()
        body.setContentsMargins(24, 12, 24, 0)
        self._pivot = Pivot(self)
        body.addWidget(self._pivot)
        self._pages = QStackedWidget(self)
        body.addWidget(self._pages, 1)
        root.addLayout(body, 1)
        self._widgets = {}
        self._page_by_route = {}
        for route, label, title, description, start, end in self.GROUPS:
            scroll = QScrollArea(self)
            scroll.setObjectName(route)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 16, 0, 16)
            layout.addWidget(SubtitleLabel(title))
            caption = CaptionLabel(description)
            caption.setWordWrap(True)
            layout.addWidget(caption)
            card = SimpleCardWidget(content)
            form = QFormLayout(card)
            form.setContentsMargins(16, 16, 16, 16)
            form.setSpacing(16)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            for key, text, typ, _ in self.FIELDS[start:end]:
                widget = self._make_widget(key, typ)
                self._widgets[key] = widget
                if key == "execution_workspace":
                    row = QWidget()
                    line = QHBoxLayout(row)
                    line.setContentsMargins(0, 0, 0, 0)
                    line.addWidget(widget, 1)
                    choose = QPushButton("选择目录")
                    choose.clicked.connect(self._choose_workspace)
                    line.addWidget(choose)
                    form.addRow(QLabel(text), row)
                else:
                    form.addRow(QLabel(text), widget)
            layout.addWidget(card)
            if route == "search":
                self._add_search_controls(layout)
            if route == "execution":
                limits_hint = CaptionLabel("限制按每次发送或重新执行计算，搜索也计入工具调用总数。"
                                           "模型每轮可调用多个工具；上述限制或活动时长 5 分钟中任一先到即停止。"
                                           "修改工具或搜索设置后需为当前对话重新启用工具。")
                limits_hint.setWordWrap(True)
                layout.addWidget(limits_hint)
                hint = CaptionLabel("自动执行只适用于允许列表中的字面量命令、已知参数和工作区内路径。"
                                    "其余有效命令需确认；"
                                    "工作区不是系统沙箱。请在通用助手输入区为当前会话启用工具。")
                hint.setWordWrap(True)
                layout.addWidget(hint)
            if route == "desktop":
                caption = CaptionLabel("截图快捷键：⌘⌃S · 系统权限仍在 macOS 系统设置中管理。")
                caption.setWordWrap(True)
                layout.addWidget(caption)
            layout.addStretch()
            scroll.setWidget(content)
            self._pages.addWidget(scroll)
            self._page_by_route[route] = scroll
            self._pivot.addItem(route, label)
        # Pivot's itemClicked signal carries a bool, not a page index. Route
        # changes cover both mouse clicks and programmatic navigation.
        self._pivot.currentItemChanged.connect(self._select_page)
        self._pages.currentChanged.connect(self._sync_page_tab)
        self._pivot.setCurrentItem("model")
        self._widgets["search_provider"].currentIndexChanged.connect(self._provider_changed)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(24, 12, 24, 20)
        self._save_status = CaptionLabel("")
        buttons.addWidget(self._save_status, 1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._save_button = PrimaryPushButton("保存")
        self._save_button.clicked.connect(self._on_save)
        buttons.addWidget(self._save_button)
        root.addLayout(buttons)

    def _select_page(self, route):
        self._pages.setCurrentWidget(self._page_by_route[route])

    def _sync_page_tab(self, index):
        page = self._pages.widget(index)
        if page is not None:
            self._pivot.setCurrentItem(page.objectName())

    def _make_widget(self, key, typ):
        if key == "think":
            return ThinkingSelector(self._current.get("think", ThinkSetting(ThinkMode.ON)), self)
        if key in self.CHOICES or key == "pet_source":
            widget = QComboBox()
            choices = self.CHOICES.get(key)
            if choices is None:
                choices = [(label, (source, name)) for label, source, name in _discover_pets()]
                if not choices:
                    choices = [("内置猫头鹰", ("built-in", "owl-v2"))]
            for text, value in choices:
                widget.addItem(text, value)
        elif typ is float:
            widget = QDoubleSpinBox()
            widget.setRange(*self.FLOAT_RANGES[key])
            widget.setSingleStep(0.05)
            widget.setDecimals(2)
        elif typ is int:
            widget = QSpinBox()
            widget.setRange(*self.INT_RANGES[key])
        elif typ is bool:
            widget = QCheckBox()
        else:
            widget = QLineEdit()
        return widget

    def _add_search_controls(self, layout):
        self._source_hint = CaptionLabel("")
        self._source_hint.setWordWrap(True)
        layout.addWidget(self._source_hint)
        layout.addWidget(SubtitleLabel("API 密钥"))
        caption = CaptionLabel("密钥仅保存在 macOS 钥匙串。留空保留已存密钥；修改将在保存时生效。")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self._key_widgets = {}
        self._key_delete = {}
        card = SimpleCardWidget()
        form = QFormLayout(card)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(12)
        for provider, label in (("parallel", "Parallel"), ("exa", "Exa")):
            key = QLineEdit()
            key.setEchoMode(QLineEdit.Password)
            key.setPlaceholderText("输入新密钥，或留空保留")
            key.setMaxLength(4096)
            clear = QCheckBox("保存时移除已存密钥")
            self._key_widgets[provider] = key
            self._key_delete[provider] = clear
            form.addRow(QLabel(label), key)
            form.addRow("", clear)
        layout.addWidget(card)
        row = QHBoxLayout()
        self._test_button = QPushButton("测试搜索")
        self._test_button.clicked.connect(self._test_search)
        self._cancel_search = QPushButton("停止测试")
        self._cancel_search.clicked.connect(self._stop_search)
        self._cancel_search.hide()
        row.addWidget(self._test_button)
        row.addWidget(self._cancel_search)
        row.addStretch()
        layout.addLayout(row)
        self._search_status = CaptionLabel("测试将向所选服务执行一次搜索，可能消耗账户额度。")
        self._search_status.setWordWrap(True)
        layout.addWidget(self._search_status)
        state = CaptionLabel("在通用助手输入区启用联网搜索后，模型可按任务需要调用所选服务。")
        state.setWordWrap(True)
        layout.addWidget(state)

    def _load(self):
        for key, _, typ, default in self.FIELDS:
            value = self._current.get(key, default)
            widget = self._widgets[key]
            if key == "think":
                widget.set_setting(value)
            elif key in self.CHOICES or key == "pet_source":
                if key == "pet_source":
                    value = (value, self._current.get("pet_name", "owl-v2"))
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
            elif typ in (int, float):
                widget.setValue(typ(default if value is None else value))
            elif typ is bool:
                widget.setChecked(default if value is None else bool(value))
            else:
                widget.setText(str(default if value is None else value))
        self._provider_changed()

    def _choose_workspace(self):
        selected = QFileDialog.getExistingDirectory(self, "选择任务工作区",
                                                    self._widgets["execution_workspace"].text())
        if selected:
            self._widgets["execution_workspace"].setText(selected)
            self._workspace_changed()

    def _workspace_changed(self):
        workspace = self._widgets['execution_workspace'].text().strip()
        self._widgets['bash_policy'].setEnabled(bool(workspace))
        self._widgets['execution_path'].setEnabled(bool(workspace))
        if workspace == self._policy_workspace:
            return
        self._policy_workspace = workspace
        policy = load_workspace_policy(workspace) if workspace else BashPolicy.READONLY_AUTO
        combo = self._widgets['bash_policy']
        combo.setCurrentIndex(max(0, combo.findData(policy.value)))

    def _thinking_model_changed(self):
        self._widgets["think"].set_model(self._widgets["base_url"].text(), self._model, self._model_version)

    def _provider_changed(self):
        parallel = self._widgets["search_provider"].currentData() == "parallel"
        self._widgets["search_parallel_mode"].setEnabled(parallel)
        self._source_hint.setText("Parallel 由服务决定返回数量，本设置限制保留数量，不保证返回指定条数。"
                                  if parallel else "Exa 按设置请求来源数量，实际可能少于请求数量。")

    def _collect(self):
        data = {}
        for key, _, typ, default in self.FIELDS:
            widget = self._widgets[key]
            if key == "think":
                data[key] = widget.currentData().record()
            elif key == "pet_source":
                data["pet_source"], data["pet_name"] = widget.currentData()
            elif key in self.CHOICES:
                data[key] = widget.currentData()
            elif typ in (float, int):
                data[key] = widget.value()
            elif typ is bool:
                data[key] = widget.isChecked()
            else:
                data[key] = widget.text().strip() or default
        return data

    def _on_save(self):
        data = self._collect()
        if not data["base_url"].startswith(("http://", "https://")):
            self._widgets["base_url"].setFocus()
            QMessageBox.warning(self, "输入错误", "Ollama 服务地址需要以 http:// 或 https:// 开头")
            return
        if "+" not in data["hotkey"] or not data["hotkey"].startswith("<"):
            self._widgets["hotkey"].setFocus()
            QMessageBox.warning(self, "输入错误", "快捷键格式无效，例如: <cmd>+<ctrl>+l")
            return
        if data['execution_workspace']:
            try:
                snapshot = ExecutionSnapshot.create(data['execution_workspace'], data['bash_policy'],
                                                    search_path=data['execution_path'].split(':'))
                data['execution_workspace'] = snapshot.workspace
            except (ValueError, OSError, RuntimeError) as exc:
                self._save_status.setText(f"工作区设置无效：{exc}")
                self._widgets['execution_workspace'].setFocus()
                return
        for provider, widget in self._key_widgets.items():
            if widget.text() and self._key_delete[provider].isChecked():
                self._save_status.setText("请为同一服务选择更新密钥或移除密钥。")
                return
            if widget.text() and any(c.isspace() for c in widget.text()):
                self._save_status.setText("密钥不能包含空白字符。")
                return
        try:
            for provider, widget in self._key_widgets.items():
                if self._key_delete[provider].isChecked():
                    self._credentials.delete(provider)
                    self._key_delete[provider].setChecked(False)
                    self._save_status.setText(f"{provider.title()} 密钥已移除")
                elif widget.text():
                    self._credentials.set(provider, widget.text())
                    widget.clear()
                    self._save_status.setText(f"{provider.title()} 密钥已保存")
        except CredentialError as exc:
            self._save_status.setText(str(exc))
            return
        self.settings_applied.emit(data)
        self.accept()

    def _test_search(self):
        if self._search_job is not None:
            return
        data = self._collect()
        provider = data["search_provider"]
        try:
            if self._key_delete[provider].isChecked():
                raise SearchError("取消移除密钥后再测试。")
            key = self._key_widgets[provider].text() or self._credentials.get(provider)
            settings = SearchSettings(provider, data["search_max_results"], data["search_timeout"],
                                      data["search_parallel_mode"])
            request = build_request(settings, key, "Python official documentation",
                                    "Find the official Python documentation.")
        except (SearchError, CredentialError) as exc:
            self._search_status.setText(str(exc))
            return
        self._search_job = SearchJob(request, self)
        self._search_job.finished.connect(self._search_finished)
        self._test_button.setEnabled(False)
        self._save_button.setEnabled(False)
        self._cancel_search.show()
        self._search_status.setText(f"正在测试 {provider.title()}…")
        self._search_job.start()

    def _search_finished(self, result):
        job, self._search_job = self._search_job, None
        if job is not None:
            job.deleteLater()
        self._test_button.setEnabled(True)
        self._save_button.setEnabled(True)
        self._cancel_search.hide()
        if result.cancelled:
            self._search_status.setText("测试已停止。")
        elif result.error:
            self._search_status.setText(result.error)
        else:
            self._search_status.setText(f"连接成功，返回 {len(result.sources)} 个有效来源。")

    def _stop_search(self):
        if self._search_job is not None:
            self._search_job.cancel()

    def done(self, result):
        self._widgets["think"].stop()
        self._stop_search()
        for widget in self._key_widgets.values():
            widget.clear()
        super().done(result)
