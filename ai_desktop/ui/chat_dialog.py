"""
对话窗口 —— Agent 多轮对话
"""
from PyQt5.QtCore import Qt, QPoint, QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from ai_desktop import config
from ai_desktop.config import Agent
from ai_desktop.ui import markdown, styles


class ChatDialog(QWidget):
    message_sent = pyqtSignal(str)  # 用户发送的消息文本
    new_convo_requested = pyqtSignal()
    agent_changed = pyqtSignal(Agent)
    model_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, agents: list[Agent], active_agent: Agent,
                 models: list[str] | None = None, active_model: str = "", parent=None):
        super().__init__(parent)
        self._agents = agents
        self._active_agent = active_agent
        self._models = models or [active_model] if active_model else []
        self._active_model = active_model
        self._drag_pos: QPoint | None = None
        self._stream_bubble: QLabel | None = None  # 当前流式输出的气泡
        self._stream_text: str = ""
        self._stream_buffer: str = ""               # 积攒的回复 token
        self._thinking_text: str = ""               # 完整思考文本
        self._thinking_buffer: str = ""             # 积攒的思考 token
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(50)          # 50ms 刷新一次
        self._stream_timer.timeout.connect(self._flush_stream_buffer)
        self._setup_window()
        self._setup_ui()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setMinimumSize(420, 500)
        self.resize(440, 580)
        self.setStyleSheet("QWidget { background: #ffffff; border-radius: 10px; }")

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(44)
        title.setStyleSheet(
            "background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px;"
        )
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)
        tl.setSpacing(6)

        icon_lbl = QLabel(self._active_agent.icon)
        icon_lbl.setStyleSheet("font-size: 18px; background: none;")
        tl.addWidget(icon_lbl)
        self._title_icon = icon_lbl

        name_lbl = QLabel(self._active_agent.name)
        name_lbl.setStyleSheet("font-weight: bold; font-size: 13px; background: none;")
        tl.addWidget(name_lbl)
        self._title_name = name_lbl

        tl.addStretch()

        # 新建对话按钮
        new_btn = QPushButton("＋ 新对话")
        new_btn.setFixedHeight(28)
        new_btn.setStyleSheet(
            "QPushButton { background: #e8e8e8; border: none; border-radius: 5px;"
            "padding: 2px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        new_btn.clicked.connect(self.new_convo_requested.emit)
        tl.addWidget(new_btn)

        # Agent 切换
        self._agent_combo = QComboBox()
        self._agent_combo.setFixedHeight(28)
        self._agent_combo.setMinimumWidth(110)
        self._agent_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._agent_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 5px; padding: 2px 6px; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView {"
            "  color: #333;"
            "  selection-background-color: #FFD700;"
            "  selection-color: #333;"
            "  outline: none;"
            "}"
        )
        for ag in self._agents:
            self._agent_combo.addItem(f"{ag.icon} {ag.name}", ag.id)
        idx = next(i for i, ag in enumerate(self._agents) if ag.id == self._active_agent.id)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.currentIndexChanged.connect(self._on_agent_combo)
        tl.addWidget(self._agent_combo)

        # 模型选择
        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(28)
        self._model_combo.setMinimumWidth(110)
        self._model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._model_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 5px; padding: 2px 6px; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView {"
            "  color: #333;"
            "  selection-background-color: #FFD700;"
            "  selection-color: #333;"
            "  outline: none;"
            "}"
        )
        self._model_combo.setToolTip("选择模型")
        for m in self._models:
            self._model_combo.addItem(m)
        if self._active_model and self._active_model in self._models:
            self._model_combo.setCurrentText(self._active_model)
        # 下拉列表宽度适应最长模型名
        self._model_combo.view().setMinimumWidth(140)
        self._model_combo.currentTextChanged.connect(self._on_model_combo)
        tl.addWidget(self._model_combo)

        root.addWidget(title)

        # ── 消息区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #ffffff; }"
            "QScrollBar:vertical { width: 6px; }"
            "QScrollBar::handle:vertical { background: #ccc; border-radius: 3px; }"
        )

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet("background: #ffffff;")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(12, 8, 12, 8)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()

        scroll.setWidget(self._msg_container)
        self._scroll = scroll
        root.addWidget(scroll, stretch=1)

        # ── 输入区域 ──
        input_bar = QWidget()
        input_bar.setFixedHeight(56)
        input_bar.setStyleSheet(
            "background: #fafafa; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
        )
        il = QHBoxLayout(input_bar)
        il.setContentsMargins(10, 8, 10, 8)
        il.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("输入消息... (Enter 发送)")
        self._input.setStyleSheet(
            "QLineEdit { border: 1px solid #ddd; border-radius: 6px; padding: 6px 10px;"
            "font-size: 13px; background: #ffffff; }"
            "QLineEdit:focus { border-color: #007AFF; }"
        )
        self._input.returnPressed.connect(self._on_send)
        il.addWidget(self._input)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(56, 36)
        self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
        self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn)

        root.addWidget(input_bar)

    # ── Agent 切换 ─────────────────────────────────────

    def _on_agent_combo(self, index: int) -> None:
        agent_id = self._agent_combo.itemData(index)
        agent = next(ag for ag in self._agents if ag.id == agent_id)
        self._active_agent = agent
        self._title_icon.setText(agent.icon)
        self._title_name.setText(agent.name)
        self.agent_changed.emit(agent)

    def _on_model_combo(self, text: str) -> None:
        self._active_model = text
        self.model_changed.emit(text)

    def set_active_agent(self, agent: Agent) -> None:
        self._active_agent = agent
        self._title_icon.setText(agent.icon)
        self._title_name.setText(agent.name)
        idx = next(i for i, ag in enumerate(self._agents) if ag.id == agent.id)
        self._agent_combo.blockSignals(True)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.blockSignals(False)

    # ── 输入 ───────────────────────────────────────────

    def set_input_text(self, text: str) -> None:
        """设置输入框文本并聚焦"""
        self._input.setText(text)
        self._input.setFocus()
        self._input.selectAll()

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self.message_sent.emit(text)
        self._input.clear()

    def add_user_message(self, text: str) -> None:
        bubble = self._make_bubble(text, is_user=True)
        self._insert_widget(bubble)

    def add_assistant_message(self, text: str) -> None:
        html = markdown.to_html(text)
        bubble = self._make_bubble(html, is_user=False, is_html=True)
        self._insert_widget(bubble)

    # ── 流式输出 ───────────────────────────────────────

    def begin_assistant_stream(self) -> None:
        """创建空的助手气泡，准备接收流式 token"""
        self._stream_text = ""
        self._stream_buffer = ""
        self._thinking_text = ""
        self._thinking_buffer = ""
        bubble = self._make_bubble("", is_user=False, is_html=True)
        lbl = bubble.findChild(QLabel)
        if lbl:
            self._stream_bubble = lbl
        self._insert_widget(bubble)
        self._stream_timer.start()

    def append_thinking_chunk(self, token: str) -> None:
        """追加思考 token 到缓冲区"""
        self._thinking_buffer += token

    def append_stream_chunk(self, token: str) -> None:
        """追加回复 token 到缓冲区，由定时器批量刷新"""
        self._stream_buffer += token

    def _flush_stream_buffer(self) -> None:
        """定时将缓冲区内容刷新到 QLabel"""
        if self._stream_bubble is None:
            return
        changed = False
        if self._thinking_buffer:
            self._thinking_text += self._thinking_buffer
            self._thinking_buffer = ""
            changed = True
        if self._stream_buffer:
            self._stream_text += self._stream_buffer
            self._stream_buffer = ""
            changed = True
        if not changed:
            return
        # 流式显示：思考文字用前缀标注
        display = ""
        if self._thinking_text:
            display += f"💭 {self._thinking_text}\n\n"
        display += self._stream_text
        self._stream_bubble.setText(display)
        self._stream_bubble.setTextFormat(Qt.PlainText)

    def finalize_assistant_stream(self, text: str, ok: bool) -> None:
        """流式结束，刷新残留并转为 Markdown HTML"""
        self._stream_timer.stop()
        self._flush_stream_buffer()
        if self._stream_bubble is None:
            return
        if ok and self._stream_text:
            body = markdown.to_html(self._stream_text)
            if self._thinking_text.strip():
                thinking_html = (
                    '<details style="margin-bottom:10px;color:#888;font-size:12px;">'
                    '<summary style="cursor:pointer;color:#666;">💭 思考过程</summary>'
                    f'<pre style="white-space:pre-wrap;word-break:break-word;margin-top:4px;">{self._thinking_text}</pre>'
                    '</details>'
                )
                body = thinking_html + body
            full_html = (
                '<html><body style="font-size:13px; font-family:'
                + config.FONT_FAMILY
                + ';">'
                + body
                + "</body></html>"
            )
            self._stream_bubble.setText(full_html)
            self._stream_bubble.setTextFormat(Qt.RichText)
        elif not ok and self._stream_text:
            self._stream_bubble.setText(f"❌ {self._stream_text}")
            self._stream_bubble.setTextFormat(Qt.PlainText)
        self._stream_bubble = None
        self._stream_text = ""
        self._stream_buffer = ""
        self._thinking_text = ""
        self._thinking_buffer = ""

    def set_thinking(self, thinking: bool) -> None:
        self._send_btn.setEnabled(not thinking)
        self._send_btn.setText("..." if thinking else "发送")
        self._input.setEnabled(not thinking)

    def clear_messages(self) -> None:
        while self._msg_layout.count() > 1:  # keep the stretch
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── 气泡 ───────────────────────────────────────────

    def _make_bubble(self, content: str, is_user: bool, is_html: bool = False) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(340)
        lbl.setTextFormat(Qt.RichText if is_html else Qt.PlainText)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)

        if is_user:
            lbl.setStyleSheet(
                "QLabel { background: #007AFF; color: white; border-radius: 10px;"
                "padding: 8px 12px; font-size: 13px; }"
            )
            wl.addStretch()
            wl.addWidget(lbl)
        else:
            lbl.setStyleSheet(
                "QLabel { background: #f0f0f0; color: #1a1a1a; border-radius: 10px;"
                "padding: 8px 12px; font-size: 13px; }"
            )
            wl.addWidget(lbl)
            wl.addStretch()

        if is_html:
            # QLabel doesn't support full HTML with inline styles well;
            # for assistant messages, embed the body into a full HTML string
            full_html = (
                '<html><body style="font-size:13px; font-family:'
                + config.FONT_FAMILY
                + ';">'
                + content
                + "</body></html>"
            )
            lbl.setText(full_html)
        else:
            lbl.setText(content)

        return wrapper

    def _insert_widget(self, w: QWidget) -> None:
        idx = self._msg_layout.count() - 1
        self._msg_layout.insertWidget(idx, w)
        QApplication.processEvents()
        sb = self._scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    # ── 定位 ───────────────────────────────────────────

    def show_near(self, anchor: QPoint) -> None:
        """在悬浮按钮左侧弹出"""
        self.adjustSize()
        x = anchor.x() - self.width() - 12
        y = anchor.y() - self.height() // 2
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            if x < geo.left():
                x = anchor.x() + 60
            if y < geo.top():
                y = geo.top() + 8
            if y + self.height() > geo.bottom():
                y = geo.bottom() - self.height() - 8
        self.move(x, y)
        self.show()
        self.activateWindow()
        self.raise_()
        self._input.setFocus()

    # ── 事件 ───────────────────────────────────────────

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event and event.key() == Qt.Key_Escape:
            self.hide()
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)
