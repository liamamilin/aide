"""
对话历史浏览窗口
"""
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.config import AGENTS, Agent
from ai_desktop.ui import styles
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.utils.storage import (
    delete_conversation,
    page_conversations,
    update_conversation_title,
)


class HistoryDialog(FramelessDragMixin, QDialog):
    PAGE_SIZE = 50
    conversation_selected = pyqtSignal(int)
    conversation_deleted = pyqtSignal(int)
    conversation_renamed = pyqtSignal(int, str)

    def __init__(self, parent=None, *, agents: list[Agent] | None = None):
        super().__init__(parent)
        self._agents = {agent.id: agent for agent in (agents or AGENTS)}
        self._query = ""
        self._cursor: tuple[float, int] | None = None
        self._loaded_ids: list[int] = []
        self._setup_drag(40)
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        # StaysOnTop: 父窗口（对话窗）常驻顶层，不加此标志会被其遮挡
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(380, 300)
        self.resize(400, 420)
        self.setStyleSheet(styles.DIALOG_BASE)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)

        title_lbl = QLabel("对话历史")
        title_lbl.setStyleSheet(styles.LABEL_BOLD)
        tl.addWidget(title_lbl)
        tl.addStretch()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(styles.CLOSE_BUTTON)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)

        root.addWidget(title)

        # ── 搜索栏 ──
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索对话…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(styles.SEARCH_FIELD)
        self._search.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search)

        # 防抖定时器（300ms）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)

        # ── 列表区域 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(styles.SCROLL_AREA)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll)

        self._load_more_btn = QPushButton("加载更多")
        self._load_more_btn.setObjectName("history_load_more")
        self._load_more_btn.setStyleSheet(styles.SECONDARY_BUTTON)
        self._load_more_btn.clicked.connect(self._load_more)
        self._load_more_btn.setVisible(False)
        root.addWidget(self._load_more_btn)

    # ── 搜索 ───────────────────────────────────────────

    def _on_search_changed(self) -> None:
        """输入变化时重置防抖定时器"""
        self._search_timer.start()

    def _do_search(self) -> None:
        """执行搜索并刷新列表"""
        query = self._search.text().strip()
        self._reset_results(query)

    def _clear_list(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── 加载 / 刷新 ────────────────────────────────────

    def _load(self) -> None:
        self._reset_results("")

    def _reset_results(self, query: str, *, target_count: int | None = None) -> None:
        self._query = query
        self._cursor = None
        self._loaded_ids = []
        self._clear_list()
        target = max(self.PAGE_SIZE, target_count or 0)
        while len(self._loaded_ids) < target:
            before = len(self._loaded_ids)
            self._append_page()
            if self._cursor is None or len(self._loaded_ids) == before:
                break
        if not self._loaded_ids:
            empty_text = "未找到匹配的对话" if query else "暂无历史对话"
            empty = QLabel(empty_text)
            empty.setObjectName("history_empty")
            empty.setStyleSheet(styles.EMPTY_STATE)
            empty.setAlignment(Qt.AlignCenter)
            self._list_layout.insertWidget(0, empty)
        self._load_more_btn.setVisible(self._cursor is not None)

    def _append_page(self) -> None:
        conversations, next_cursor = page_conversations(
            limit=self.PAGE_SIZE,
            cursor=self._cursor,
            query=self._query,
        )

        for convo in conversations:
            if convo["id"] in self._loaded_ids:
                continue
            row = self._make_row(convo)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._loaded_ids.append(convo["id"])
        self._cursor = next_cursor
        self._load_more_btn.setVisible(self._cursor is not None)

    def _load_more(self) -> None:
        if self._cursor is not None:
            self._append_page()

    def _refresh(self) -> None:
        query = self._search.text().strip()
        target = max(self.PAGE_SIZE, len(self._loaded_ids))
        scrollbar = self._scroll.verticalScrollBar()
        position = scrollbar.value()
        self._reset_results(query, target_count=target)
        QTimer.singleShot(
            0,
            lambda: scrollbar.setValue(min(position, scrollbar.maximum())),
        )

    def _make_row(self, convo: dict) -> QWidget:
        row = QWidget()
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(styles.HISTORY_ROW)

        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(8)

        # Agent 图标
        agent = self._agents.get(convo["agent_id"])
        icon_text = agent.icon if agent else "💬"
        icon = QLabel(icon_text)
        icon.setFixedWidth(24)
        icon.setStyleSheet(styles.TITLE_ICON)
        rl.addWidget(icon)

        # 标题 + 副标题
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        title_lbl = QLabel(convo["title"] or "(空对话)")
        title_lbl.setObjectName("history_title")
        title_lbl.setProperty("conversation_id", convo["id"])
        title_lbl.setStyleSheet(styles.LABEL)
        text_col.addWidget(title_lbl)

        dt = datetime.fromtimestamp(convo["created_at"])
        agent_name = agent.name if agent else "未知 Agent"
        subtitle = (
            f"{agent_name} · {dt.month}月{dt.day}日 · "
            f"{convo['msg_count']}条消息"
        )
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(styles.LABEL_SECONDARY)
        text_col.addWidget(sub_lbl)

        rl.addLayout(text_col, stretch=1)

        rename_btn = QPushButton("✏️")
        rename_btn.setFixedSize(24, 24)
        rename_btn.setToolTip("重命名对话")
        rename_btn.setStyleSheet(styles.HISTORY_DELETE_BUTTON)
        rename_btn.clicked.connect(
            lambda checked, item=convo: self._on_rename(item["id"], item["title"])
        )
        rl.addWidget(rename_btn)

        # 删除按钮
        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(24, 24)
        del_btn.setToolTip("删除对话")
        del_btn.setStyleSheet(styles.HISTORY_DELETE_BUTTON)
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
        reply = QMessageBox.question(
            self, "删除对话", "确定删除该对话？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        delete_conversation(convo_id)
        self.conversation_deleted.emit(convo_id)
        self._refresh()

    def _on_rename(self, convo_id: int, current_title: str) -> None:
        title, accepted = QInputDialog.getText(
            self,
            "重命名对话",
            "标题（最多 80 个字符）：",
            QLineEdit.Normal,
            current_title,
        )
        if not accepted:
            return
        try:
            normalized = update_conversation_title(convo_id, title)
        except (ValueError, LookupError) as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))
            return
        self.conversation_renamed.emit(convo_id, normalized)
        self._refresh()

    # ── 拖拽 / Esc ──（由 FramelessDragMixin 处理）──
