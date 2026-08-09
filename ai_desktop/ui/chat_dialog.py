"""
对话窗口 —— Agent 多轮对话
"""
import html
import logging

import requests
from PyQt5.QtCore import QEvent, QPoint, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeyEvent, QPainterPath, QRegion, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
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
from ai_desktop.ui.controls import PolishedComboBox
from ai_desktop.ui.float_button import pin_to_all_spaces
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.ui.theme import current

logger = logging.getLogger(__name__)


class ChatDialog(FramelessDragMixin, QWidget):
    message_sent = pyqtSignal(str)
    new_convo_requested = pyqtSignal()
    history_requested = pyqtSignal()
    export_requested = pyqtSignal()
    manage_agents_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    speak_requested = pyqtSignal(str)
    stop_speaking_requested = pyqtSignal()
    agent_changed = pyqtSignal(Agent)
    model_changed = pyqtSignal(str)
    ollama_online = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, agents: list[Agent], active_agent: Agent,
                 models: list[str] | None = None, active_model: str = "",
                 auto_hide: bool = False, parent=None):
        super().__init__(parent)
        self._setup_drag(44)
        self._agents = agents
        self._active_agent = active_agent
        self._auto_hide = auto_hide
        self._models = list(models) if models else []
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
        # 输入历史浏览状态
        self._input_history: list[str] = []         # 最新在前
        self._hist_index = -1                       # -1 = 未在浏览
        self._hist_draft = ""                       # 进入浏览前保存的草稿
        self._bubble_labels: list[QLabel] = []
        self._layout_compact: bool | None = None
        self._tts_active = False
        self._scroll_pending = False
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(12)
        self._resize_timer.timeout.connect(self._update_responsive_layout)
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
        self.setObjectName("chatDialog")
        self.setStyleSheet(styles.CHAT_DIALOG_ROOT)
        self._apply_rounded_mask()

    def _apply_rounded_mask(self) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_scroll"):
            self._resize_timer.start()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Row 1: 标题栏 (40px) ──
        title = QWidget()
        title.setFixedHeight(44)
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
        self._toolbar.setFixedHeight(48)
        self._toolbar.setStyleSheet(styles.TOOLBAR)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(10, 8, 10, 8)
        tb.setSpacing(8)

        # Agent 切换
        self._agent_combo = PolishedComboBox()
        self._agent_combo.setMinimumWidth(104)
        self._agent_combo.setMaximumWidth(142)
        self._agent_combo.setSizeAdjustPolicy(PolishedComboBox.AdjustToMinimumContentsLengthWithIcon)
        for ag in self._agents:
            self._agent_combo.addItem(ag.name, ag.id)
        idx = next(i for i, ag in enumerate(self._agents) if ag.id == self._active_agent.id)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.currentIndexChanged.connect(self._on_agent_combo)
        self._agent_combo.setToolTip(self._active_agent.name)
        tb.addWidget(self._agent_combo)

        # 模型选择
        self._model_combo = PolishedComboBox()
        self._model_combo.setMinimumWidth(120)
        self._model_combo.setMaximumWidth(190)
        self._model_combo.setSizeAdjustPolicy(PolishedComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._model_combo.setToolTip(self._active_model or "选择模型")
        if not self._models:
            self._model_combo.addItem("加载中…")
            self._model_combo.setEnabled(False)
        else:
            for m in self._models:
                self._model_combo.addItem(m)
            if self._active_model and self._active_model in self._models:
                self._model_combo.setCurrentText(self._active_model)
        self._model_combo.currentTextChanged.connect(self._on_model_combo)
        tb.addWidget(self._model_combo)

        tb.addStretch()

        new_btn = QPushButton("＋ 新对话")
        new_btn.setFixedHeight(32)
        new_btn.setStyleSheet(styles.SECONDARY_BUTTON)
        new_btn.clicked.connect(self.new_convo_requested.emit)
        new_btn.setToolTip("新对话 (⌃N)")
        self._new_btn = new_btn
        tb.addWidget(new_btn)

        self._history_btn = QPushButton(self._toolbar)
        self._history_btn.clicked.connect(self.history_requested.emit)
        self._history_btn.hide()
        self._export_btn = QPushButton(self._toolbar)
        self._export_btn.clicked.connect(self.export_requested.emit)
        self._export_btn.hide()

        more_btn = QPushButton("•••")
        more_btn.setFixedSize(32, 32)
        more_btn.setToolTip("更多操作")
        more_btn.setStyleSheet(styles.ICON_BUTTON)
        more_menu = QMenu(more_btn)
        more_menu.setStyleSheet(styles.MENU)
        more_menu.addAction("对话历史", lambda: self.history_requested.emit())
        more_menu.addAction("复制为 Markdown", lambda: self.export_requested.emit())
        more_menu.addSeparator()
        more_menu.addAction("管理 Agent", lambda: self.manage_agents_requested.emit())
        more_btn.setMenu(more_menu)
        self._more_btn = more_btn
        tb.addWidget(more_btn)

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
        self._empty_state = QLabel(
            f"{self._active_agent.icon}\n\n开始与 {self._active_agent.name} 对话\n输入问题，或粘贴选中的文字",
            self._msg_container,
        )
        self._empty_state.setObjectName("chatEmptyState")
        self._empty_state.setAlignment(Qt.AlignCenter)
        self._empty_state.setStyleSheet(styles.EMPTY_STATE)
        self._empty_state.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._empty_state.raise_()
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
        self._input.setFixedHeight(40)
        self._input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input.setStyleSheet(styles.INPUT_AREA)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._on_input_text_changed)
        il.addWidget(self._input, stretch=1)

        self._tts_btn = QPushButton("🔊")
        self._tts_btn.setFixedSize(36, 40)
        self._tts_btn.setStyleSheet(styles.ICON_BUTTON)
        self._tts_btn.setToolTip("朗读选中文字（未选择时朗读全部输入）")
        self._tts_btn.clicked.connect(self._on_speak_input)
        il.addWidget(self._tts_btn)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(58, 40)
        self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
        self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn)

        # Ollama 状态指示器（10px 圆形）
        self._ollama_dot = QWidget()
        self._ollama_dot.setFixedSize(10, 10)
        self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS)
        self._ollama_dot.setToolTip("检测中…")
        il.addWidget(self._ollama_dot)

        self._generation_status = QLabel("正在生成…")
        self._generation_status.setStyleSheet(styles.STATUS_TEXT)
        self._generation_status.setVisible(False)
        il.addWidget(self._generation_status)

        input_row.addWidget(input_bar, stretch=1)

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("QSizeGrip { image: none; }")
        input_row.addWidget(grip, alignment=Qt.AlignBottom | Qt.AlignRight)

        root.addLayout(input_row)
        QTimer.singleShot(0, self._update_responsive_layout)

    # ── Agent 切换 ─────────────────────────────────────

    def _on_agent_combo(self, index: int) -> None:
        agent_id = self._agent_combo.itemData(index)
        agent = next(ag for ag in self._agents if ag.id == agent_id)
        self._active_agent = agent
        self._title_icon.setText(agent.icon)
        self._title_name.setText(agent.name)
        self._agent_combo.setToolTip(agent.name)
        self._update_empty_state()
        self.agent_changed.emit(agent)

    def _on_model_combo(self, text: str) -> None:
        self._active_model = text
        self._model_combo.setToolTip(text or "选择模型")
        self.model_changed.emit(text)

    def refresh_models(self, models: list[str]) -> None:
        """外部传入新模型列表时刷新 combo，尽量保留当前选中。

        - 列表为空：保持原状（不清空占位）
        - 当前选中仍存在：保留
        - 当前选中不存在（或原为占位）：切到第一个并 emit model_changed
        """
        if not models:
            return
        self._models = list(models)
        current = self._active_model or self._model_combo.currentText()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.setEnabled(True)
        for m in self._models:
            self._model_combo.addItem(m)
        if current in self._models:
            self._model_combo.setCurrentText(current)
            changed = False
        else:
            self._model_combo.setCurrentText(self._models[0])
            changed = True
        self._active_model = self._model_combo.currentText()
        self._model_combo.setToolTip(self._active_model)
        self._model_combo.blockSignals(False)
        if changed:
            self.model_changed.emit(self._active_model)

    def set_active_agent(self, agent: Agent) -> None:
        self._active_agent = agent
        self._title_icon.setText(agent.icon)
        self._title_name.setText(agent.name)
        self._agent_combo.setToolTip(agent.name)
        self._update_empty_state()
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

    def set_input_history(self, entries: list[str]) -> None:
        """灌入历史输入（最新在前），供上下键浏览。"""
        self._input_history = []
        seen: set[str] = set()
        for text in entries:
            text = text.strip()
            if text and text not in seen:
                seen.add(text)
                self._input_history.append(text)

    def add_input_history(self, text: str) -> None:
        """记录一条已发送的输入（重复项移到最前）。"""
        text = text.strip()
        if not text:
            return
        if text in self._input_history:
            self._input_history.remove(text)
        self._input_history.insert(0, text)
        if len(self._input_history) > 100:
            self._input_history.pop()

    def _exit_input_browsing(self) -> None:
        """退出历史浏览状态（保留当前文本）"""
        self._hist_index = -1
        self._hist_draft = ""

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
        self.add_input_history(text)
        self._exit_input_browsing()

    def _on_speak_input(self) -> None:
        if self._tts_active:
            self.stop_speaking_requested.emit()
            return
        cursor = self._input.textCursor()
        text = cursor.selectedText() if cursor.hasSelection() else self._input.toPlainText()
        self._emit_speech(text)

    def _on_speak_label(self, label: QLabel) -> None:
        if self._tts_active:
            self.stop_speaking_requested.emit()
            return
        self._emit_speech(label.selectedText() if label.hasSelectedText() else "")

    def _emit_speech(self, text: str) -> None:
        text = text.replace("\u2029", "\n").strip()[:config.TTS_MAX_TEXT_LENGTH]
        if text:
            self.speak_requested.emit(text)

    def set_tts_status(self, status: str) -> None:
        """Update the always-visible speech button for loading/playback state."""
        self._tts_active = bool(status)
        if not status:
            self._tts_btn.setText("🔊")
            self._tts_btn.setToolTip("朗读选中文字（未选择时朗读全部输入）")
        elif "朗读" in status:
            self._tts_btn.setText("⏹")
            self._tts_btn.setToolTip(f"{status} 点击停止")
        else:
            self._tts_btn.setText("…")
            self._tts_btn.setToolTip(f"{status} 点击取消")

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
            self.ollama_online.emit()
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
        if self._stream_bubble is not None:
            self._stream_bubble.setText("正在思考…")
        self._generation_status.setVisible(True)
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
                colors = current()
                thinking_html = (
                    f'<details style="color:{colors.text_secondary};font-size:12px;">'
                    f'<summary style="color:{colors.text_secondary};">💭 思考过程</summary>'
                    f'<pre style="white-space:pre-wrap;margin-top:4px;color:{colors.text_secondary};">{thinking}</pre>'
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
        self._generation_status.setVisible(False)

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
            self._generation_status.setVisible(True)
        else:
            self._send_btn.setText("发送")
            self._send_btn.setToolTip("")
            self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
            self._send_btn.clicked.connect(self._on_send)
            self._generation_status.setVisible(False)
        self._input.setEnabled(not thinking)
        if not thinking:
            self._input.setFocus()

    def clear_messages(self) -> None:
        while self._msg_layout.count() > 1:  # keep the stretch
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._bubble_labels.clear()
        self._empty_state.setVisible(True)
        self._update_empty_state()

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
        lbl.setObjectName("messageBubble")
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(self._bubble_max_width())
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
            btn_bar.setObjectName("messageActions")
            btn_bar.setFixedHeight(0)
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

            speak_btn = self._make_speak_button(lbl)
            bl.addWidget(speak_btn)

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
            btn_bar.setObjectName("messageActions")
            btn_bar.setFixedHeight(0)
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

            speak_btn = self._make_speak_button(lbl)
            bl.addWidget(speak_btn)

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

        self._bubble_labels.append(lbl)
        return wrapper

    def _make_speak_button(self, label: QLabel) -> QPushButton:
        button = QPushButton("🔊")
        button.setFixedSize(18, 18)
        button.setToolTip("朗读选中的文字")
        button.setFocusPolicy(Qt.NoFocus)
        button.setObjectName("speak_btn")
        button.setCursor(Qt.PointingHandCursor)
        button.setVisible(False)
        button.setStyleSheet(styles.COPY_BUTTON)
        button.clicked.connect(lambda checked, lbl=label: self._on_speak_label(lbl))
        return button

    # ── hover 显示复制按钮 ─────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            for btn in obj.findChildren(QPushButton, "copy_btn_assistant"):
                btn.setVisible(True)
            for btn in obj.findChildren(QPushButton, "edit_btn_user"):
                btn.setVisible(True)
            for btn in obj.findChildren(QPushButton, "speak_btn"):
                btn.setVisible(True)
            for bar in obj.findChildren(QWidget, "messageActions"):
                bar.setFixedHeight(20)
        elif event.type() == QEvent.Leave:
            for btn in obj.findChildren(QPushButton, "copy_btn_assistant"):
                btn.setVisible(False)
            for btn in obj.findChildren(QPushButton, "edit_btn_user"):
                btn.setVisible(False)
            for btn in obj.findChildren(QPushButton, "speak_btn"):
                btn.setVisible(False)
            for bar in obj.findChildren(QWidget, "messageActions"):
                bar.setFixedHeight(0)

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
            # Up/Down: 输入历史浏览（readline 式，多行时光标边界规则）
            if event.key() in (Qt.Key_Up, Qt.Key_Down):
                # macOS 的箭头键会携带 KeypadModifier，屏蔽后再判断修饰键
                if (event.modifiers() & ~Qt.KeypadModifier) == Qt.NoModifier:
                    if self._hist_handle_arrow(event.key() == Qt.Key_Up):
                        return True

        return super().eventFilter(obj, event)

    # ── 输入历史浏览 ───────────────────────────────────

    def _hist_handle_arrow(self, is_up: bool) -> bool:
        """处理上下键，返回 True 表示事件已消费。

        规则：
        - 未浏览时：按 Up 即进入浏览（保存当前内容为草稿），手动输入同样支持
        - 浏览中：光标在首行才切上一条，末行才切下一条，否则放行默认光标移动
        - 最新一条再按 Down → 恢复进入前的草稿并退出浏览
        """
        if not self._input_history:
            return False
        cursor = self._input.textCursor()
        doc = self._input.document()
        at_first_line = cursor.blockNumber() == 0
        at_last_line = cursor.blockNumber() == doc.blockCount() - 1

        if self._hist_index < 0:
            if not is_up:
                return False
            self._hist_draft = self._input.toPlainText()
            self._hist_index = 0
        else:
            if is_up and not at_first_line:
                return False
            if not is_up and not at_last_line:
                return False
            if is_up:
                if self._hist_index >= len(self._input_history) - 1:
                    return True  # 已是最旧
                self._hist_index += 1
            else:
                if self._hist_index == 0:
                    draft = self._hist_draft
                    self._exit_input_browsing()
                    self._hist_set_text(draft)
                    return True
                self._hist_index -= 1

        self._hist_set_text(self._input_history[self._hist_index])
        return True

    def _hist_set_text(self, text: str) -> None:
        self._input.setPlainText(text)
        cursor = self._input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._input.setTextCursor(cursor)

    def _on_input_text_changed(self) -> None:
        """编辑不退出浏览模式；仅自适应高度。"""
        self._adjust_input_height()

    # ── 插入气泡 ───────────────────────────────────────

    def _insert_widget(self, w: QWidget) -> None:
        self._empty_state.setVisible(False)
        idx = self._msg_layout.count() - 1
        self._msg_layout.insertWidget(idx, w)
        self._scroll_to_bottom()

    def _bubble_max_width(self) -> int:
        """Keep bubbles readable and proportional to the live viewport."""
        viewport_width = self._scroll.viewport().width() if hasattr(self, "_scroll") else self.width() - 24
        return max(220, min(560, int(viewport_width * 0.78)))

    def _update_empty_state(self) -> None:
        if not hasattr(self, "_empty_state"):
            return
        self._empty_state.setText(
            f"{self._active_agent.icon}\n\n开始与 {self._active_agent.name} 对话\n输入问题，或粘贴选中的文字"
        )
        width = max(200, self._msg_container.width() - 48)
        y = max(24, (self._scroll.viewport().height() - 120) // 2)
        self._empty_state.setGeometry(24, y, width, 120)

    def _update_responsive_layout(self) -> None:
        """Keep the primary toolbar balanced at every supported width."""
        self._apply_rounded_mask()
        compact = self.width() < 490
        if compact != self._layout_compact:
            self._layout_compact = compact
            self._new_btn.setText("＋" if compact else "＋ 新对话")
            self._new_btn.setFixedWidth(32 if compact else 82)
            self._agent_combo.setFixedWidth(110 if compact else 130)
            self._model_combo.setFixedWidth(160 if compact else 200)
        for label in tuple(self._bubble_labels):
            try:
                label.setMaximumWidth(self._bubble_max_width())
                label.updateGeometry()
            except RuntimeError:
                self._bubble_labels.remove(label)
        self._update_empty_state()

    def _scroll_to_bottom(self) -> None:
        if self._user_scrolled_up or self._scroll_pending:
            return
        self._scroll_pending = True
        QTimer.singleShot(0, self._apply_scroll_to_bottom)

    def _apply_scroll_to_bottom(self) -> None:
        self._scroll_pending = False
        if self._user_scrolled_up:
            return
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
