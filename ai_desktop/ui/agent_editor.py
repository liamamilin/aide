"""
Agent 管理窗口 —— 新增 / 编辑 / 删除自定义 Agent
"""
from dataclasses import dataclass

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ai_desktop.services.action_service import Action
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.ui.action_settings_dialog import ActionSettingsDialog
from ai_desktop.ui.fluent import BodyLabel as QLabel
from ai_desktop.ui.fluent import (
    CaptionLabel,
    PrimaryPushButton,
    SimpleCardWidget,
    TransparentPushButton,
    dialog_title,
)
from ai_desktop.ui.fluent import ComboBox as QComboBox
from ai_desktop.ui.fluent import FluentDialog as QDialog
from ai_desktop.ui.fluent import LineEdit as QLineEdit
from ai_desktop.ui.fluent import PlainTextEdit as QPlainTextEdit
from ai_desktop.ui.fluent import PushButton as QPushButton
from ai_desktop.ui.fluent import ScrollArea as QScrollArea
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.ui.model_profile_dialog import ModelProfileDialog


@dataclass
class AgentDef:
    id: str
    name: str
    icon: str
    system_prompt: str
    builtin: bool = False
    profile_id: str | None = None


class AgentEditor(FramelessDragMixin, QDialog):

    agents_saved = pyqtSignal(list)
    profiles_saved = pyqtSignal(list)
    agent_profile_changed = pyqtSignal(str, object)
    actions_saved = pyqtSignal(list)

    def __init__(self, builtin_agents: list[AgentDef], custom_agents: list[AgentDef],
                 parent=None, *, profiles: list[ModelProfile] | None = None,
                 models: list[str] | None = None,
                 actions: list[Action] | None = None,
                 global_model="", base_url="", model_versions=None):
        super().__init__(parent)
        self._setup_drag(40)
        self._builtin = builtin_agents
        self._custom = list(custom_agents)
        self._profiles = list(profiles or [])
        self._models = list(models or [])
        self._thinking_context = dict(global_model=global_model, base_url=base_url, model_versions=model_versions)
        self._actions = list(actions or [])
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(420, 340)
        self.resize(440, 400)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        root.addWidget(dialog_title(self, '管理 Agent'))

        # ── 列表区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_container)
        root.addWidget(scroll)

        # ── 新增按钮 ──
        add_bar = QWidget()
        al = QHBoxLayout(add_bar)
        al.setContentsMargins(12, 0, 12, 0)

        add_btn = QPushButton("＋ 新增 Agent")
        add_btn.clicked.connect(self._on_add)
        al.addWidget(add_btn)

        profiles_btn = TransparentPushButton("模型配置…")
        profiles_btn.clicked.connect(self._on_manage_profiles)
        al.addWidget(profiles_btn)

        actions_btn = TransparentPushButton("快捷动作…")
        actions_btn.clicked.connect(self._on_manage_actions)
        al.addWidget(actions_btn)

        root.addWidget(add_bar)

    # ── 加载 ───────────────────────────────────────────

    def _load(self) -> None:
        all_agents = list(self._builtin) + self._custom
        if not all_agents:
            return
        for ag in all_agents:
            row = self._make_row(ag)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _refresh(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._load()

    def _make_row(self, agent: AgentDef) -> QWidget:
        row = SimpleCardWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(8)

        icon = QLabel(agent.icon)
        icon.setFixedWidth(24)
        rl.addWidget(icon)

        name = QLabel(agent.name)
        rl.addWidget(name, stretch=1)

        profile = next((item for item in self._profiles if item.id == agent.profile_id), None)
        profile_tag = CaptionLabel(profile.name if profile else "全局设置")
        profile_tag.setToolTip("此 Agent 使用的模型配置")
        rl.addWidget(profile_tag)

        profile_btn = TransparentPushButton("配置")
        profile_btn.clicked.connect(lambda checked, a=agent: self._on_profile(a))
        rl.addWidget(profile_btn)

        if agent.builtin:
            tag = CaptionLabel("内置")
            rl.addWidget(tag)
        else:
            edit_btn = TransparentPushButton("编辑")
            edit_btn.clicked.connect(lambda checked, a=agent: self._on_edit(a))
            rl.addWidget(edit_btn)

            del_btn = TransparentPushButton("删除")
            del_btn.clicked.connect(lambda checked, a=agent: self._on_delete(a))
            rl.addWidget(del_btn)

        return row

    # ── 操作 ───────────────────────────────────────────

    def _on_add(self) -> None:
        dlg = _AgentEditDialog("新增 Agent", AgentDef(id="", name="", icon="🤖", system_prompt=""), self)
        if dlg.exec_() == QDialog.Accepted:
            new = AgentDef(
                id=self._next_custom_id(),
                name=dlg.name(),
                icon=dlg.icon_text(),
                system_prompt=dlg.prompt(),
                builtin=False,
                profile_id=None,
            )
            self._custom.append(new)
            self._refresh()
            self._emit_save()

    def _on_edit(self, agent: AgentDef) -> None:
        dlg = _AgentEditDialog("编辑 Agent", agent, self)
        if dlg.exec_() == QDialog.Accepted:
            agent.name = dlg.name()
            agent.icon = dlg.icon_text()
            agent.system_prompt = dlg.prompt()
            self._refresh()
            self._emit_save()

    def _on_delete(self, agent: AgentDef) -> None:
        self._custom.remove(agent)
        self._refresh()
        self._emit_save()

    def _on_profile(self, agent: AgentDef) -> None:
        dialog = _AgentProfileDialog(agent, self._profiles, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        agent.profile_id = dialog.profile_id()
        self.agent_profile_changed.emit(agent.id, agent.profile_id)
        if not agent.builtin:
            self._emit_save()
        self._refresh()

    def _on_manage_profiles(self) -> None:
        dialog = ModelProfileDialog(self._profiles, self._models, self, **self._thinking_context)
        dialog.profiles_saved.connect(self._on_profiles_updated)
        dialog.exec_()

    def _on_manage_actions(self) -> None:
        dialog = ActionSettingsDialog(
            self._actions,
            list(self._builtin) + self._custom,
            self._profiles,
            self,
        )
        dialog.actions_saved.connect(self._on_actions_updated)
        dialog.exec_()

    def _on_actions_updated(self, actions: list[Action]) -> None:
        self._actions = list(actions)
        self.actions_saved.emit(self._actions)

    def _on_profiles_updated(self, profiles: list[ModelProfile]) -> None:
        self._profiles = list(profiles)
        self.profiles_saved.emit(self._profiles)
        valid_ids = {profile.id for profile in self._profiles}
        for agent in list(self._builtin) + self._custom:
            if agent.profile_id and agent.profile_id not in valid_ids:
                agent.profile_id = None
                self.agent_profile_changed.emit(agent.id, None)
        self._refresh()

    def _emit_save(self) -> None:
        data = [
            {"id": a.id, "name": a.name, "icon": a.icon,
             "system_prompt": a.system_prompt, "profile_id": a.profile_id}
            for a in self._custom
        ]
        self.agents_saved.emit(data)

    def _next_custom_id(self) -> str:
        existing = {a.id for a in self._custom}
        i = 1
        while f"custom_{i}" in existing:
            i += 1
        return f"custom_{i}"

# ── 拖拽 / Esc ──（由 FramelessDragMixin 处理）──


class _AgentProfileDialog(FramelessDragMixin, QDialog):
    def __init__(self, agent: AgentDef, profiles: list[ModelProfile], parent=None):
        super().__init__(parent)
        self._setup_drag(36)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(dialog_title(self, f'{agent.icon} {agent.name} · 模型配置'))

        body = SimpleCardWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addWidget(QLabel("选择配置；全局设置会跟随主窗口当前模型。"))
        self._combo = QComboBox()
        self._combo.addItem("全局设置", None)
        for profile in profiles:
            self._combo.addItem(profile.name, profile.id)
        self._combo.setCurrentIndex(max(0, self._combo.findData(agent.profile_id)))
        layout.addWidget(self._combo)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = PrimaryPushButton("应用")
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        root.addWidget(body)

    def profile_id(self) -> str | None:
        return self._combo.currentData()


class _AgentEditDialog(FramelessDragMixin, QDialog):

    def __init__(self, title: str, agent: AgentDef, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 300)
        self.resize(380, 360)
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._setup_drag(36)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        root.addWidget(dialog_title(self, title))

        # 主体面板（不透明背景，避免半透明窗口下间隙穿透）
        body = SimpleCardWidget()
        broot = QVBoxLayout(body)
        broot.setContentsMargins(0, 0, 0, 0)
        broot.setSpacing(0)

        # 表单
        form = QVBoxLayout()
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(10)

        form.addWidget(QLabel("名称"))
        self._name = QLineEdit()
        self._name.setText(agent.name)
        form.addWidget(self._name)

        form.addWidget(QLabel("图标 (emoji)"))
        # 图标行：输入框 + 选择按钮
        icon_row = QHBoxLayout()
        icon_row.setSpacing(6)
        self._icon = QLineEdit()
        self._icon.setText(agent.icon)
        self._icon.setFixedWidth(50)
        icon_row.addWidget(self._icon)

        pick_btn = QPushButton("…")
        pick_btn.setToolTip("选择图标")
        pick_btn.clicked.connect(self._pick_emoji)
        icon_row.addWidget(pick_btn)
        icon_row.addStretch()
        form.addLayout(icon_row)

        form.addWidget(QLabel("System Prompt"))
        self._prompt = QPlainTextEdit()
        self._prompt.setPlainText(agent.system_prompt)
        form.addWidget(self._prompt, stretch=1)

        broot.addLayout(form)

        # 按钮
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = PrimaryPushButton("保存")
        save.clicked.connect(self._on_save)
        bb.addWidget(save)

        broot.addLayout(bb)

        root.addWidget(body)

    def _on_save(self) -> None:
        if not self._name.text().strip():
            return
        self.accept()

    def name(self) -> str:
        return self._name.text().strip()

    def icon_text(self) -> str:
        t = self._icon.text().strip()
        return t if t else "🤖"

    def prompt(self) -> str:
        return self._prompt.toPlainText().strip()

    def _pick_emoji(self) -> None:
        dlg = _EmojiPicker(self)
        if dlg.exec_() == QDialog.Accepted:
            self._icon.setText(dlg.selected_emoji)


class _EmojiPicker(FramelessDragMixin, QDialog):
    """Emoji 分类选择面板"""

    CATEGORIES = [
        ("常用", ["💻", "🤖", "🌐", "🎨", "📝", "🔧", "🎯", "💡", "🚀", "⭐"]),
        ("表情", ["😊", "😂", "🤔", "😎", "🥳", "😢", "😡", "👍", "👏", "🙏"]),
        ("物品", ["📱", "💻", "🖥️", "⌨️", "🖱️", "📷", "🎥", "📺", "🔊", "💿"]),
        ("自然", ["🔥", "☀️", "🌙", "⭐", "🌈", "❄️", "🌸", "🌊", "🌍", "🍀"]),
        ("符号", ["✅", "❌", "➕", "➖", "➡️", "🔄", "⚠️", "💯", "🔒", "❤️"]),
        ("办公", ["📊", "📈", "📋", "📌", "✂️", "📁", "🗂️", "📎", "🔗", "💼"]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_emoji = "🤖"
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(370, 300)
        self.resize(370, 320)
        self._setup_drag(36)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        root.addWidget(dialog_title(self, '选择图标'))

        # 分类区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        for label, emojis in self.CATEGORIES:
            layout.addWidget(QLabel(label))
            grid = QGridLayout()
            grid.setSpacing(4)
            for i, emoji in enumerate(emojis):
                btn = QPushButton(emoji)
                btn.clicked.connect(lambda checked, e=emoji: self._select(e))
                grid.addWidget(btn, 0, i)
            layout.addLayout(grid)

        layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll)

    def _select(self, emoji: str) -> None:
        self.selected_emoji = emoji
        self.accept()

    # ── 拖拽 / Esc ──（由 FramelessDragMixin 处理）──
