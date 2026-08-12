"""
Agent 管理窗口 —— 新增 / 编辑 / 删除自定义 Agent
"""
from dataclasses import dataclass

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.ui import styles
from ai_desktop.ui.frameless_mixin import FramelessDragMixin, TitleBar


@dataclass
class AgentDef:
    id: str
    name: str
    icon: str
    system_prompt: str
    builtin: bool = False


class AgentEditor(FramelessDragMixin, QDialog):

    agents_saved = pyqtSignal(list)

    def __init__(self, builtin_agents: list[AgentDef], custom_agents: list[AgentDef],
                 parent=None):
        super().__init__(parent)
        self._setup_drag(44)
        self._builtin = builtin_agents
        self._custom = list(custom_agents)
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(420, 340)
        self.resize(440, 400)
        self.setStyleSheet(styles.DIALOG_BASE)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = TitleBar("管理 Agent", height=44)
        title.close_clicked.connect(self.close)
        root.addWidget(title)

        # ── 列表区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(styles.SCROLL_AREA)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_container)
        root.addWidget(scroll)

        # ── 新增按钮 ──
        add_bar = QWidget()
        add_bar.setFixedHeight(44)
        add_bar.setStyleSheet(styles.AGENT_LIST_BAR)
        al = QHBoxLayout(add_bar)
        al.setContentsMargins(12, 0, 12, 0)

        add_btn = QPushButton("＋ 新增 Agent")
        add_btn.setFixedHeight(28)
        add_btn.setStyleSheet(styles.ADD_AGENT_BUTTON)
        add_btn.clicked.connect(self._on_add)
        al.addWidget(add_btn)

        root.addWidget(add_bar)

    # ── 加载 ───────────────────────────────────────────

    def _load(self) -> None:
        all_agents = list(self._builtin) + self._custom
        if not all_agents:
            empty = QLabel("还没有 Agent\n点击下方按钮创建一个")
            empty.setObjectName("agentEmptyState")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(styles.EMPTY_STATE)
            self._list_layout.insertWidget(0, empty)
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
        row = QWidget()
        row.setObjectName("listRow")
        row.setStyleSheet(styles.HISTORY_ROW)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 8, 10, 8)
        rl.setSpacing(8)

        icon = QLabel(agent.icon)
        icon.setFixedWidth(24)
        icon.setStyleSheet(styles.TITLE_ICON)
        rl.addWidget(icon)

        name = QLabel(agent.name)
        name.setStyleSheet(styles.LABEL)
        rl.addWidget(name, stretch=1)

        if agent.builtin:
            tag = QLabel("内置")
            tag.setStyleSheet(styles.LABEL_SECONDARY)
            rl.addWidget(tag)
        else:
            edit_btn = QPushButton("编辑")
            edit_btn.setFixedHeight(24)
            edit_btn.setStyleSheet(styles.AGENT_EDIT_BUTTON)
            edit_btn.clicked.connect(lambda checked, a=agent: self._on_edit(a))
            rl.addWidget(edit_btn)

            del_btn = QPushButton("删除")
            del_btn.setFixedHeight(24)
            del_btn.setStyleSheet(styles.DELETE_BUTTON)
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

    def _emit_save(self) -> None:
        data = [
            {"id": a.id, "name": a.name, "icon": a.icon, "system_prompt": a.system_prompt}
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


class _AgentEditDialog(FramelessDragMixin, QDialog):

    def __init__(self, title: str, agent: AgentDef, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 300)
        self.resize(380, 360)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(styles.DIALOG_BASE)
        self._setup_drag(36)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        tbar = QWidget()
        tbar.setFixedHeight(36)
        tbar.setStyleSheet(styles.TITLE_BAR)
        tl2 = QHBoxLayout(tbar)
        tl2.setContentsMargins(12, 0, 8, 0)
        tl2.addWidget(QLabel(title))
        tl2.addStretch()
        cb = QPushButton("×")
        cb.setFixedSize(24, 24)
        cb.setStyleSheet(styles.CLOSE_BUTTON)
        cb.clicked.connect(self.reject)
        tl2.addWidget(cb)
        root.addWidget(tbar)

        # 表单
        form = QVBoxLayout()
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(10)

        form.addWidget(QLabel("名称"))
        self._name = QLineEdit(agent.name)
        self._name.setStyleSheet(styles.FORM_INPUT)
        form.addWidget(self._name)

        form.addWidget(QLabel("图标 (emoji)"))
        # 图标行：输入框 + 选择按钮
        icon_row = QHBoxLayout()
        icon_row.setSpacing(6)
        self._icon = QLineEdit(agent.icon)
        self._icon.setFixedWidth(50)
        self._icon.setStyleSheet(styles.FORM_INPUT)
        icon_row.addWidget(self._icon)

        pick_btn = QPushButton("…")
        pick_btn.setFixedSize(28, 28)
        pick_btn.setToolTip("选择图标")
        pick_btn.setStyleSheet(styles.EMOJI_PICK_BUTTON)
        pick_btn.clicked.connect(self._pick_emoji)
        icon_row.addWidget(pick_btn)
        icon_row.addStretch()
        form.addLayout(icon_row)

        form.addWidget(QLabel("System Prompt"))
        self._prompt = QPlainTextEdit()
        self._prompt.setPlainText(agent.system_prompt)
        self._prompt.setStyleSheet(styles.FORM_TEXTAREA)
        form.addWidget(self._prompt, stretch=1)

        root.addLayout(form)

        # 按钮
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.setStyleSheet(styles.CANCEL_BUTTON)
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = QPushButton("保存")
        save.setStyleSheet(styles.SAVE_BUTTON)
        save.clicked.connect(self._on_save)
        bb.addWidget(save)

        root.addLayout(bb)

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
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(370, 300)
        self.resize(370, 320)
        self.setStyleSheet(styles.DIALOG_BASE)
        self._setup_drag(36)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        tbar = QWidget()
        tbar.setFixedHeight(36)
        tbar.setStyleSheet(styles.TITLE_BAR)
        tl2 = QHBoxLayout(tbar)
        tl2.setContentsMargins(12, 0, 8, 0)
        tl2.addWidget(QLabel("选择图标"))
        tl2.addStretch()
        cb = QPushButton("×")
        cb.setFixedSize(24, 24)
        cb.setStyleSheet(styles.CLOSE_BUTTON)
        cb.clicked.connect(self.reject)
        tl2.addWidget(cb)
        root.addWidget(tbar)

        # 分类区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(styles.SCROLL_AREA)
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
                btn.setFixedSize(34, 34)
                btn.setStyleSheet(styles.EMOJI_GRID_BUTTON)
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
