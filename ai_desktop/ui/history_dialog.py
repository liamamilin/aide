"""
对话历史浏览窗口
"""
from datetime import datetime

from PyQt5.QtCore import Qt, QPoint, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.config import AGENTS
from ai_desktop.utils.storage import delete_conversation, list_conversations_with_counts


class HistoryDialog(QDialog):
    conversation_selected = pyqtSignal(int)  # emits conversation id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setMinimumSize(380, 300)
        self.resize(400, 420)
        self.setStyleSheet(
            "QDialog { background: #ffffff; border-radius: 10px; }"
        )

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

        title_lbl = QLabel("对话历史")
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

    # ── 加载 / 刷新 ────────────────────────────────────

    def _load(self) -> None:
        conversations = list_conversations_with_counts(limit=50)
        if not conversations:
            empty = QLabel("暂无历史对话")
            empty.setStyleSheet("color: #999; font-size: 13px; padding: 20px;")
            empty.setAlignment(Qt.AlignCenter)
            self._list_layout.insertWidget(0, empty)
            return

        for convo in conversations:
            row = self._make_row(convo)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

    def _refresh(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._load()

    def _make_row(self, convo: dict) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(
            "QWidget { background: transparent; border-radius: 6px; }"
            "QWidget:hover { background: #f0f0f0; }"
        )

        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(8)

        # Agent 图标
        agent = next((a for a in AGENTS if a.id == convo["agent_id"]), None)
        icon_text = agent.icon if agent else "💬"
        icon = QLabel(icon_text)
        icon.setFixedWidth(24)
        icon.setStyleSheet("font-size: 16px; background: none;")
        rl.addWidget(icon)

        # 标题 + 副标题
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(convo["title"] or "(空对话)")
        title_lbl.setStyleSheet("font-size: 13px; color: #1a1a1a; background: none;")
        text_col.addWidget(title_lbl)

        dt = datetime.fromtimestamp(convo["created_at"])
        subtitle = f"{dt.month}月{dt.day}日 · {convo['msg_count']}条消息"
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet("font-size: 11px; color: #999; background: none;")
        text_col.addWidget(sub_lbl)

        rl.addLayout(text_col, stretch=1)

        # 删除按钮
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(24, 24)
        del_btn.setToolTip("删除对话")
        del_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 12px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.08); border-radius: 12px; }"
        )
        del_btn.clicked.connect(lambda checked, cid=convo["id"]: self._on_delete(cid))
        rl.addWidget(del_btn)

        # 点击行 → 选中对话
        cid = convo["id"]
        row.mousePressEvent = lambda e, cid=cid: self._on_select(cid)

        return row

    def _on_select(self, convo_id: int) -> None:
        self.conversation_selected.emit(convo_id)
        self.close()

    def _on_delete(self, convo_id: int) -> None:
        delete_conversation(convo_id)
        self._refresh()

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
