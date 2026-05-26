"""
Agent 管理窗口 —— 新增 / 编辑 / 删除自定义 Agent
"""
from dataclasses import dataclass

from PyQt5.QtCore import Qt, QPoint, pyqtSignal
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


@dataclass
class AgentDef:
    id: str
    name: str
    icon: str
    system_prompt: str
    builtin: bool = False


class AgentEditor(QDialog):
    """管理 Agent 列表"""

    agents_saved = pyqtSignal(list)  # 保存后发射新的自定义 Agent 列表

    def __init__(self, builtin_agents: list[AgentDef], custom_agents: list[AgentDef],
                 parent=None):
        super().__init__(parent)
        self._builtin = builtin_agents
        self._custom = list(custom_agents)
        self._drag_pos: QPoint | None = None
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setMinimumSize(420, 340)
        self.resize(440, 400)
        self.setStyleSheet("QDialog { background: #ffffff; border-radius: 10px; }")

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(
            "background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px;"
        )
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)

        title_lbl = QLabel("管理 Agent")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; background: none; color: #1a1a1a;")
        tl.addWidget(title_lbl)
        tl.addStretch()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 16px; color: #999; }"
            "QPushButton:hover { color: #333; }"
        )
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)

        root.addWidget(title)

        # ── 列表区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #ffffff; }"
            "QScrollBar:vertical { width: 6px; }"
            "QScrollBar::handle:vertical { background: #ccc; border-radius: 3px; }"
        )

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
        add_bar.setStyleSheet("background: #fafafa; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;")
        al = QHBoxLayout(add_bar)
        al.setContentsMargins(12, 0, 12, 0)

        add_btn = QPushButton("＋ 新增 Agent")
        add_btn.setFixedHeight(28)
        add_btn.setStyleSheet(
            "QPushButton { background: #007AFF; color: white; border: none; border-radius: 5px;"
            "padding: 2px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #0066d6; }"
        )
        add_btn.clicked.connect(self._on_add)
        al.addWidget(add_btn)

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
        row = QWidget()
        row.setStyleSheet("QWidget { background: transparent; }")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(8)

        icon = QLabel(agent.icon)
        icon.setFixedWidth(24)
        icon.setStyleSheet("font-size: 16px; background: none;")
        rl.addWidget(icon)

        name = QLabel(agent.name)
        name.setStyleSheet("font-size: 13px; color: #1a1a1a; background: none;")
        rl.addWidget(name, stretch=1)

        if agent.builtin:
            tag = QLabel("内置")
            tag.setStyleSheet("font-size: 11px; color: #999; background: none;")
            rl.addWidget(tag)
        else:
            edit_btn = QPushButton("编辑")
            edit_btn.setFixedHeight(24)
            edit_btn.setStyleSheet(
                "QPushButton { background: #e8e8e8; border: none; border-radius: 4px;"
                "padding: 2px 8px; font-size: 11px; color: #333; }"
                "QPushButton:hover { background: #d0d0d0; }"
            )
            edit_btn.clicked.connect(lambda checked, a=agent: self._on_edit(a))
            rl.addWidget(edit_btn)

            del_btn = QPushButton("删除")
            del_btn.setFixedHeight(24)
            del_btn.setStyleSheet(
                "QPushButton { background: #e8e8e8; border: none; border-radius: 4px;"
                "padding: 2px 8px; font-size: 11px; color: #c00; }"
                "QPushButton:hover { background: #fdd; }"
            )
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

    # ── 拖拽 / Esc ─────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.pos().y() <= 40:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)


class _AgentEditDialog(QDialog):
    """单个 Agent 编辑表单"""

    def __init__(self, title: str, agent: AgentDef, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 300)
        self.resize(380, 360)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setStyleSheet("QDialog { background: #ffffff; border-radius: 10px; }")
        self._drag_pos: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        tbar = QWidget()
        tbar.setFixedHeight(36)
        tbar.setStyleSheet("background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px;")
        tl2 = QHBoxLayout(tbar)
        tl2.setContentsMargins(12, 0, 8, 0)
        tl2.addWidget(QLabel(title))
        tl2.addStretch()
        cb = QPushButton("×")
        cb.setFixedSize(24, 24)
        cb.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 16px; color: #999; }"
                         "QPushButton:hover { color: #333; }")
        cb.clicked.connect(self.reject)
        tl2.addWidget(cb)
        root.addWidget(tbar)

        # 表单
        form = QVBoxLayout()
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(10)

        form.addWidget(QLabel("名称"))
        self._name = QLineEdit(agent.name)
        self._name.setStyleSheet("QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 4px 8px; }"
                                  "QLineEdit:focus { border-color: #007AFF; }")
        form.addWidget(self._name)

        form.addWidget(QLabel("图标 (emoji)"))
        # 图标行：输入框 + 选择按钮
        icon_row = QHBoxLayout()
        icon_row.setSpacing(6)
        self._icon = QLineEdit(agent.icon)
        self._icon.setFixedWidth(50)
        self._icon.setStyleSheet("QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 4px 8px; font-size: 18px; }"
                                  "QLineEdit:focus { border-color: #007AFF; }")
        icon_row.addWidget(self._icon)

        pick_btn = QPushButton("…")
        pick_btn.setFixedSize(28, 28)
        pick_btn.setToolTip("选择图标")
        pick_btn.setStyleSheet(
            "QPushButton { background: #f0f0f0; border: 1px solid #ddd; border-radius: 4px;"
            "font-size: 14px; color: #333; }"
            "QPushButton:hover { background: #e0e0e0; }"
        )
        pick_btn.clicked.connect(self._pick_emoji)
        icon_row.addWidget(pick_btn)
        icon_row.addStretch()
        form.addLayout(icon_row)

        form.addWidget(QLabel("System Prompt"))
        self._prompt = QPlainTextEdit()
        self._prompt.setPlainText(agent.system_prompt)
        self._prompt.setStyleSheet("QPlainTextEdit { border: 1px solid #ddd; border-radius: 4px; padding: 6px 8px;"
                                    "font-size: 12px; font-family: Menlo, monospace; }"
                                    "QPlainTextEdit:focus { border-color: #007AFF; }")
        form.addWidget(self._prompt, stretch=1)

        root.addLayout(form)

        # 按钮
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.setStyleSheet("QPushButton { background: #e8e8e8; border: none; border-radius: 4px; padding: 6px 16px; }"
                             "QPushButton:hover { background: #d0d0d0; }")
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = QPushButton("保存")
        save.setStyleSheet("QPushButton { background: #007AFF; color: white; border: none; border-radius: 4px; padding: 6px 16px; }"
                           "QPushButton:hover { background: #0066d6; }")
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

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.pos().y() <= 36:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)


class _EmojiPicker(QDialog):
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
        self.setMinimumSize(370, 300)
        self.resize(370, 320)
        self.setStyleSheet("QDialog { background: #ffffff; border-radius: 10px; }")
        self._drag_pos: QPoint | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        tbar = QWidget()
        tbar.setFixedHeight(36)
        tbar.setStyleSheet("background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px;")
        tl2 = QHBoxLayout(tbar)
        tl2.setContentsMargins(12, 0, 8, 0)
        tl2.addWidget(QLabel("选择图标"))
        tl2.addStretch()
        cb = QPushButton("×")
        cb.setFixedSize(24, 24)
        cb.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 16px; color: #999; }"
                         "QPushButton:hover { color: #333; }")
        cb.clicked.connect(self.reject)
        tl2.addWidget(cb)
        root.addWidget(tbar)

        # 分类区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: #ffffff; }")
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
                btn.setStyleSheet(
                    "QPushButton { font-size: 17px; border: 1px solid #ddd; border-radius: 4px; background: #fff; }"
                    "QPushButton:hover { background: #f0f0f0; border-color: #007AFF; }"
                )
                btn.clicked.connect(lambda checked, e=emoji: self._select(e))
                grid.addWidget(btn, 0, i)
            layout.addLayout(grid)

        layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll)

    def _select(self, emoji: str) -> None:
        self.selected_emoji = emoji
        self.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.pos().y() <= 36:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)
