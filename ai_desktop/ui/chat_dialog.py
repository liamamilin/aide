"""
对话窗口 —— Agent 多轮对话
"""
import html
import logging

import requests
from PyQt5.QtCore import QEvent, QPoint, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeyEvent, QPainterPath, QRegion
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ai_desktop import config
from ai_desktop.capture.text_normalizer import normalize
from ai_desktop.config import Agent
from ai_desktop.ui import markdown, styles
from ai_desktop.ui.float_button import pin_to_all_spaces
from ai_desktop.ui.frameless_mixin import FramelessDragMixin

logger = logging.getLogger(__name__)


class ChatDialog(FramelessDragMixin, QWidget):
    message_sent = pyqtSignal(str)
    new_convo_requested = pyqtSignal()
    history_requested = pyqtSignal()
    export_requested = pyqtSignal()
    manage_agents_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    agent_changed = pyqtSignal(Agent)
    model_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, agents: list[Agent], active_agent: Agent,
                 models: list[str] | None = None, active_model: str = "",
                 auto_hide: bool = False, parent=None):
        super().__init__(parent)
        self._setup_drag(40)
        self._agents = agents
        self._active_agent = active_agent
        self._auto_hide = auto_hide
        self._models = models or [active_model] if active_model else []
        self._active_model = active_model
        self._user_scrolled_up: bool = False
        self._stream_bubble: QLabel | None = None
        self._stream_copy_btn: QPushButton | None = None
        self._stream_text: str = ""
        self._stream_buffer: str = ""               # 积攒的回复 token
        self._thinking_text: str = ""               # 完整思考文本
        self._thinking_buffer: str = ""             # 积攒的思考 token
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(50)          # 50ms 刷新一次
        self._stream_timer.timeout.connect(self._flush_stream_buffer)
        self._ollama_timer = QTimer(self)
        self._ollama_timer.setInterval(30000)       # 每 30 秒探活
        self._ollama_timer.timeout.connect(self._check_ollama_status)
        self._setup_window()
        self._setup_ui()

    def _default_size(self) -> tuple[int, int]:
        screen = QApplication.primaryScreen()
        if screen is None:
            return (440, 580)
        geo = screen.availableGeometry()
        w = max(400, min(int(geo.width() * 0.30), 520))
        h = max(500, min(int(geo.height() * 0.60), 800))
        return (w, h)

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        w, h = self._default_size()
        self.setMinimumSize(400, 460)
        self.resize(w, h)
        self._apply_rounded_mask()
        self.setStyleSheet(styles.CHAT_DIALOG_ROOT)

    def _apply_rounded_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_mask()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Row 1: 标题栏 (40px) ──
        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 4, 0)
        tl.setSpacing(6)

        icon_lbl = QLabel(self._active_agent.icon)
        icon_lbl.setStyleSheet(styles.TITLE_ICON)
        tl.addWidget(icon_lbl)
        self._title_icon = icon_lbl

        name_lbl = QLabel(self._active_agent.name)
        name_lbl.setStyleSheet(styles.TITLE_NAME)
        tl.addWidget(name_lbl)
        self._title_name = name_lbl

        tl.addStretch()

        hide_btn = QPushButton("−")
        hide_btn.setFixedSize(24, 24)
        hide_btn.setStyleSheet(styles.ICON_BUTTON)
        hide_btn.clicked.connect(self.hide)
        tl.addWidget(hide_btn)

        root.addWidget(title)

        # ── Row 2: 工具栏 (36px, 可折叠) ──
        self._toolbar = QWidget()
        self._toolbar.setFixedHeight(36)
        self._toolbar.setStyleSheet(styles.TOOLBAR)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(8, 0, 8, 0)
        tb.setSpacing(6)

        # Agent 切换
        self._agent_combo = QComboBox()
        self._agent_combo.setFixedHeight(26)
        self._agent_combo.setMinimumWidth(100)
        self._agent_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._agent_combo.setStyleSheet(styles.COMBO_BOX)
        for ag in self._agents:
            self._agent_combo.addItem(f"{ag.icon} {ag.name}", ag.id)
        idx = next(i for i, ag in enumerate(self._agents) if ag.id == self._active_agent.id)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.currentIndexChanged.connect(self._on_agent_combo)
        tb.addWidget(self._agent_combo)

        # 模型选择
        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(26)
        self._model_combo.setMinimumWidth(80)
        self._model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._model_combo.setStyleSheet(styles.MODEL_COMBO_BOX)
        self._model_combo.setToolTip("选择模型")
        for m in self._models:
            self._model_combo.addItem(m)
        if self._active_model and self._active_model in self._models:
            self._model_combo.setCurrentText(self._active_model)
        self._model_combo.currentTextChanged.connect(self._on_model_combo)
        tb.addWidget(self._model_combo)

        tb.addStretch()

        new_btn = QPushButton("＋ 新对话")
        new_btn.setFixedHeight(26)
        new_btn.setStyleSheet(styles.SECONDARY_BUTTON)
        new_btn.clicked.connect(self.new_convo_requested.emit)
        tb.addWidget(new_btn)

        hist_btn = QPushButton("📋 历史")
        hist_btn.setFixedHeight(26)
        hist_btn.setStyleSheet(styles.SECONDARY_BUTTON)
        hist_btn.clicked.connect(self.history_requested.emit)
        tb.addWidget(hist_btn)

        self._export_btn = QPushButton("📤 导出")
        self._export_btn.setFixedHeight(26)
        self._export_btn.setStyleSheet(styles.SECONDARY_BUTTON)
        self._export_btn.clicked.connect(self.export_requested.emit)
        tb.addWidget(self._export_btn)

        gear_btn = QPushButton("⚙")
        gear_btn.setFixedSize(24, 24)
        gear_btn.setToolTip("管理 Agent")
        gear_btn.setStyleSheet(styles.ICON_BUTTON)
        gear_btn.clicked.connect(self.manage_agents_requested.emit)
        tb.addWidget(gear_btn)

        root.addWidget(self._toolbar)

        # ── 消息区域 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(styles.SCROLL_AREA)

        self._msg_container = QWidget()
        self._msg_container.setStyleSheet(styles.MESSAGE_LIST)
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(12, 8, 12, 8)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()

        scroll.setWidget(self._msg_container)
        self._scroll = scroll
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        root.addWidget(scroll, stretch=1)

        # ── 输入区域 ──
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(0)

        input_bar = QWidget()
        input_bar.setStyleSheet(styles.INPUT_BAR)
        il = QHBoxLayout(input_bar)
        il.setContentsMargins(8, 6, 8, 6)
        il.setSpacing(6)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("输入消息... (Enter 发送, Shift+Enter 换行)")
        self._input.setFixedHeight(36)
        self._input.setStyleSheet(styles.INPUT_AREA)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._adjust_input_height)
        il.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(56, 36)
        self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
        self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn)

        # Ollama 状态指示器（10px 圆形）
        self._ollama_dot = QWidget()
        self._ollama_dot.setFixedSize(10, 10)
        self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS)
        self._ollama_dot.setToolTip("检测中…")
        il.addWidget(self._ollama_dot)

        input_row.addWidget(input_bar, stretch=1)

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("QSizeGrip { image: none; }")
        input_row.addWidget(grip, alignment=Qt.AlignBottom | Qt.AlignRight)

        root.addLayout(input_row)

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

    def refresh_agents(self, agents: list[Agent]) -> None:
        """刷新 Agent 下拉列表（自定义 Agent 变更后调用）"""
        self._agents = agents
        self._agent_combo.blockSignals(True)
        self._agent_combo.clear()
        for ag in agents:
            self._agent_combo.addItem(f"{ag.icon} {ag.name}", ag.id)
        idx = next(i for i, ag in enumerate(agents) if ag.id == self._active_agent.id)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.blockSignals(False)

    def set_auto_hide(self, enabled: bool) -> None:
        self._auto_hide = enabled

    # ── 输入 ───────────────────────────────────────────

    def set_input_text(self, text: str) -> None:
        self._input.setPlainText(text)
        self._input.setFocus()
        self._input.selectAll()

    def flash_busy(self) -> None:
        self._input.setPlaceholderText("⏳ 等待回复完成...")
        QTimer.singleShot(1500, lambda: self._input.setPlaceholderText("输入消息... (Enter 发送, Shift+Enter 换行)"))

    def flash_export_btn(self) -> None:
        self._export_btn.setText("✅ 已复制")
        QTimer.singleShot(1500, lambda: self._export_btn.setText("📤 导出"))

    def _on_send(self) -> None:
        text = normalize(self._input.toPlainText())
        if not text:
            return
        self.message_sent.emit(text)
        self._input.clear()

    def _adjust_input_height(self) -> None:
        doc = self._input.document()
        total_lines = 0
        block = doc.begin()
        while block.isValid():
            total_lines += block.layout().lineCount()
            block = block.next()
        line_h = self._input.fontMetrics().lineSpacing()
        h = total_lines * line_h + 18  # padding(8+8) + border(1+1)
        new_h = max(36, min(120, h))
        cur_h = self._input.height()
        if cur_h != new_h:
            self._input.setFixedHeight(new_h)

    def _check_ollama_status(self) -> None:
        self._ping_worker = _OllamaPingWorker()
        self._ping_worker.result.connect(self._on_ollama_result)
        self._ping_worker.start()

    def _on_ollama_result(self, ok: bool) -> None:
        if ok:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_OK)
            self._ollama_dot.setToolTip("Ollama 已连接")
        else:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_ERR)
            self._ollama_dot.setToolTip("Ollama 未连接")

    def add_user_message(self, text: str) -> None:
        bubble = self._make_bubble(text, is_user=True)
        self._insert_widget(bubble)

    def add_assistant_message(self, text: str) -> None:
        html, code_map = markdown.to_html(text)
        bubble = self._make_bubble(html, is_user=False, is_html=True, code_map=code_map)
        btn = bubble.findChild(QPushButton, "copy_btn_assistant")
        if btn:
            btn.clicked.connect(lambda checked, t=text: self._copy_to_clipboard(t))
        self._insert_widget(bubble)

    # ── 流式输出 ───────────────────────────────────────

    def begin_assistant_stream(self) -> None:
        """创建空的助手气泡，准备接收流式 token"""
        self._user_scrolled_up = False  # 新回复开始，恢复自动跟随
        self._stream_text = ""
        self._stream_buffer = ""
        self._thinking_text = ""
        self._thinking_buffer = ""
        self._stream_copy_btn = None
        bubble = self._make_bubble("", is_user=False, is_html=True)
        lbl = bubble.findChild(QLabel)
        if lbl:
            self._stream_bubble = lbl
        btn = bubble.findChild(QPushButton, "copy_btn_assistant")
        if btn:
            self._stream_copy_btn = btn
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
        self._scroll_to_bottom()

    def finalize_assistant_stream(self, text: str, ok: bool) -> None:
        """流式结束，刷新残留并转为 Markdown HTML"""
        self._stream_timer.stop()
        self._flush_stream_buffer()
        if self._stream_bubble is None:
            return
        if ok and self._stream_text:
            body, code_map = markdown.to_html(self._stream_text)
            if self._thinking_text.strip():
                thinking = html.escape(self._thinking_text, quote=False)
                thinking_html = (
                    '<details style="margin-bottom:10px;color:#888;font-size:12px;">'
                    '<summary style="cursor:pointer;color:#666;">💭 思考过程</summary>'
                    f'<pre style="white-space:pre-wrap;word-break:break-word;margin-top:4px;">{thinking}</pre>'
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
            if code_map:
                self._stream_bubble.code_map = code_map
                self._stream_bubble.linkActivated.connect(self._on_link_activated)
        elif not ok and self._stream_text:
            self._stream_bubble.setText(f"❌ {self._stream_text}")
            self._stream_bubble.setTextFormat(Qt.PlainText)

        # 连接复制按钮
        if self._stream_copy_btn:
            copy_text = self._stream_text
            try:
                self._stream_copy_btn.clicked.disconnect()
            except TypeError:
                pass
            self._stream_copy_btn.clicked.connect(
                lambda checked, t=copy_text: self._copy_to_clipboard(t)
            )
            self._stream_copy_btn.setVisible(True)

        self._scroll_to_bottom()
        self._stream_bubble = None
        self._stream_copy_btn = None
        self._stream_text = ""
        self._stream_buffer = ""
        self._thinking_text = ""
        self._thinking_buffer = ""
        self._input.setFocus()

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            pass

    def set_thinking(self, thinking: bool) -> None:
        self._send_btn.setEnabled(True)
        try:
            self._send_btn.clicked.disconnect()
        except TypeError:
            pass
        if thinking:
            self._send_btn.setText("⏹")
            self._send_btn.setToolTip("停止生成")
            self._send_btn.setStyleSheet(styles.STOP_BUTTON)
            self._send_btn.clicked.connect(self.stop_requested.emit)
        else:
            self._send_btn.setText("发送")
            self._send_btn.setToolTip("")
            self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
            self._send_btn.clicked.connect(self._on_send)
        self._input.setEnabled(not thinking)
        if not thinking:
            self._input.setFocus()

    def clear_messages(self) -> None:
        while self._msg_layout.count() > 1:  # keep the stretch
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── 气泡 ───────────────────────────────────────────

    def _make_bubble(
        self, content: str, is_user: bool, is_html: bool = False,
        code_map: dict[str, str] | None = None,
    ) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel()
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(340)
        lbl.setTextFormat(Qt.RichText if is_html else Qt.PlainText)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

        if is_html and code_map:
            lbl.linkActivated.connect(self._on_link_activated)
            lbl.code_map = code_map

        if is_user:
            lbl.setStyleSheet(styles.USER_BUBBLE)
            # 用户气泡 + 编辑按钮（hover 显示）
            v_layout = QVBoxLayout()
            v_layout.setContentsMargins(0, 0, 0, 0)
            v_layout.setSpacing(2)
            v_layout.addWidget(lbl)

            btn_bar = QWidget()
            btn_bar.setFixedHeight(22)
            bl = QHBoxLayout(btn_bar)
            bl.setContentsMargins(4, 0, 8, 0)
            bl.addStretch()

            edit_btn = QPushButton("✏️")
            edit_btn.setFixedSize(18, 18)
            edit_btn.setToolTip("编辑消息")
            edit_btn.setFocusPolicy(Qt.NoFocus)
            edit_btn.setObjectName("edit_btn_user")
            edit_btn.setCursor(Qt.PointingHandCursor)
            edit_btn.setVisible(False)
            edit_btn.setStyleSheet(styles.EDIT_BUTTON)
            edit_btn.clicked.connect(lambda checked, t=content: self.set_input_text(t))
            bl.addWidget(edit_btn)

            v_layout.addWidget(btn_bar)
            wl.addStretch()
            wl.addLayout(v_layout)

            wrapper.installEventFilter(self)
        else:
            lbl.setStyleSheet(styles.ASSISTANT_BUBBLE)
            # 气泡主体 + 复制按钮（hover 显示）
            v_layout = QVBoxLayout()
            v_layout.setContentsMargins(0, 0, 0, 0)
            v_layout.setSpacing(2)
            v_layout.addWidget(lbl)

            btn_bar = QWidget()
            btn_bar.setFixedHeight(22)
            bl = QHBoxLayout(btn_bar)
            bl.setContentsMargins(4, 0, 8, 0)
            bl.addStretch()

            copy_btn = QPushButton("📋")
            copy_btn.setFixedSize(18, 18)
            copy_btn.setToolTip("复制回复")
            copy_btn.setFocusPolicy(Qt.NoFocus)
            copy_btn.setObjectName("copy_btn_assistant")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setVisible(False)
            copy_btn.setStyleSheet(styles.COPY_BUTTON)
            bl.addWidget(copy_btn)

            v_layout.addWidget(btn_bar)
            wl.addLayout(v_layout)
            wl.addStretch()

            wrapper.installEventFilter(self)

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

    # ── hover 显示复制按钮 ─────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            for btn in obj.findChildren(QPushButton, "copy_btn_assistant"):
                btn.setVisible(True)
            for btn in obj.findChildren(QPushButton, "edit_btn_user"):
                btn.setVisible(True)
        elif event.type() == QEvent.Leave:
            for btn in obj.findChildren(QPushButton, "copy_btn_assistant"):
                btn.setVisible(False)
            for btn in obj.findChildren(QPushButton, "edit_btn_user"):
                btn.setVisible(False)

        # ── 输入框快捷键 ──
        if obj is self._input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                if self._input.toPlainText().strip():
                    self._input.clear()
                    return True
                return False
            # Enter 发送（无修饰键时），Shift+Enter 换行由 QPlainTextEdit 默认处理
            if (event.key() == Qt.Key_Return
                    and event.modifiers() == Qt.NoModifier):
                self._on_send()
                return True
            if (event.key() == Qt.Key_N and
                    event.modifiers() == Qt.ControlModifier):
                self.new_convo_requested.emit()
                return True

        return super().eventFilter(obj, event)

    # ── 插入气泡 ───────────────────────────────────────

    def _insert_widget(self, w: QWidget) -> None:
        idx = self._msg_layout.count() - 1
        self._msg_layout.insertWidget(idx, w)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        if self._user_scrolled_up:
            return
        QApplication.processEvents()
        sb = self._scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _on_scroll_changed(self, value: int) -> None:
        sb = self._scroll.verticalScrollBar()
        if sb and value < sb.maximum() - 10:
            self._user_scrolled_up = True
        else:
            self._user_scrolled_up = False

    # ── 定位 ───────────────────────────────────────────

    def show_near(self, anchor: QPoint) -> None:
        """在悬浮按钮左侧弹出"""
        if not self.isVisible():
            w, h = self._default_size()
            self.resize(w, h)
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
        pin_to_all_spaces(self)
        self.activateWindow()
        self.raise_()
        self._input.setFocus()
        # 重新打开时滚动到底部显示最新消息
        self._user_scrolled_up = False
        self._scroll_to_bottom()
        self._ollama_timer.start()
        self._check_ollama_status()  # 打开时立即探活

    # ── 事件 ───────────────────────────────────────────

    def _on_link_activated(self, url: str) -> None:
        lbl = self.sender()
        code_map = getattr(lbl, "code_map", {})
        code = code_map.get(url, "")
        if code:
            self._copy_to_clipboard(code)

    def changeEvent(self, event) -> None:
        if self._auto_hide and event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event and event.key() == Qt.Key_Escape:
            if self._input.toPlainText().strip():
                self._input.clear()
            else:
                self.hide()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self._ollama_timer.stop()
        self.closed.emit()
        super().hideEvent(event)


class _OllamaPingWorker(QThread):
    """后台线程探活 Ollama"""
    result = pyqtSignal(bool)

    def run(self) -> None:
        try:
            r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=1)
            self.result.emit(r.status_code == 200)
        except Exception:
            self.result.emit(False)
