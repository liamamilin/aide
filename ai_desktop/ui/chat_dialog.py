"""
对话窗口 —— Agent 多轮对话
"""
import requests
from PyQt5.QtCore import Qt, QPoint, QEvent, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ai_desktop import config
from ai_desktop.config import Agent
from ai_desktop.ui import markdown, styles
from ai_desktop.ui.float_button import pin_to_all_spaces


class ChatDialog(QWidget):
    message_sent = pyqtSignal(str)  # 用户发送的消息文本
    new_convo_requested = pyqtSignal()
    history_requested = pyqtSignal()  # 打开历史对话框
    export_requested = pyqtSignal()   # 导出当前对话
    manage_agents_requested = pyqtSignal()  # 管理 Agent
    stop_requested = pyqtSignal()   # 停止流式生成
    agent_changed = pyqtSignal(Agent)
    model_changed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, agents: list[Agent], active_agent: Agent,
                 models: list[str] | None = None, active_model: str = "",
                 auto_hide: bool = False, parent=None):
        super().__init__(parent)
        self._agents = agents
        self._active_agent = active_agent
        self._auto_hide = auto_hide
        self._models = models or [active_model] if active_model else []
        self._active_model = active_model
        self._drag_pos: QPoint | None = None
        self._user_scrolled_up: bool = False  # 用户手动滚离底部时暂停自动跟随
        self._stream_bubble: QLabel | None = None  # 当前流式输出的气泡
        self._stream_copy_btn: QPushButton | None = None  # 当前流式气泡的复制按钮
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

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setMinimumSize(420, 500)
        self.resize(440, 580)
        self.setStyleSheet("QWidget { background: #ffffff; color: #1a1a1a; border-radius: 10px; }")

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(44)
        title.setStyleSheet(
            "background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px; color: #1a1a1a;"
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
            "padding: 2px 10px; font-size: 11px; color: #333; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        new_btn.clicked.connect(self.new_convo_requested.emit)
        tl.addWidget(new_btn)

        # 历史按钮
        hist_btn = QPushButton("📋 历史")
        hist_btn.setFixedHeight(28)
        hist_btn.setStyleSheet(
            "QPushButton { background: #e8e8e8; border: none; border-radius: 5px;"
            "padding: 2px 10px; font-size: 11px; color: #333; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        hist_btn.clicked.connect(self.history_requested.emit)
        tl.addWidget(hist_btn)

        # 导出按钮
        self._export_btn = QPushButton("📤 导出")
        self._export_btn.setFixedHeight(28)
        self._export_btn.setStyleSheet(
            "QPushButton { background: #e8e8e8; border: none; border-radius: 5px;"
            "padding: 2px 10px; font-size: 11px; color: #333; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        self._export_btn.clicked.connect(self.export_requested.emit)
        tl.addWidget(self._export_btn)

        # Agent 切换
        self._agent_combo = QComboBox()
        self._agent_combo.setFixedHeight(28)
        self._agent_combo.setMinimumWidth(110)
        self._agent_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._agent_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 5px; padding: 2px 6px;"
            "  font-size: 11px; color: #333; background: #ffffff; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView {"
            "  color: #333;"
            "  background: #ffffff;"
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

        # Agent 管理按钮
        gear_btn = QPushButton("⚙")
        gear_btn.setFixedSize(24, 24)
        gear_btn.setToolTip("管理 Agent")
        gear_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 14px; color: #999; }"
            "QPushButton:hover { color: #333; background: #e0e0e0; border-radius: 12px; }"
        )
        gear_btn.clicked.connect(self.manage_agents_requested.emit)
        tl.addWidget(gear_btn)

        # 模型选择
        self._model_combo = QComboBox()
        self._model_combo.setFixedHeight(28)
        self._model_combo.setMinimumWidth(80)
        self._model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._model_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 5px; padding: 2px 4px;"
            "  font-size: 11px; color: #333; background: #ffffff; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView {"
            "  color: #333;"
            "  background: #ffffff;"
            "  selection-background-color: #FFD700;"
            "  selection-color: #333;"
            "  outline: none;"
            "  min-width: 120px;"
            "}"
        )
        self._model_combo.setToolTip("选择模型")
        for m in self._models:
            self._model_combo.addItem(m)
        if self._active_model and self._active_model in self._models:
            self._model_combo.setCurrentText(self._active_model)
        self._model_combo.currentTextChanged.connect(self._on_model_combo)
        tl.addWidget(self._model_combo)

        # 收起按钮
        hide_btn = QPushButton("−")
        hide_btn.setFixedSize(24, 24)
        hide_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 16px; color: #999; }"
            "QPushButton:hover { color: #333; background: #e0e0e0; border-radius: 12px; }"
        )
        hide_btn.clicked.connect(self.hide)
        tl.addWidget(hide_btn)

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
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
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
            "font-size: 13px; background: #ffffff; color: #1a1a1a; }"
            "QLineEdit:focus { border-color: #007AFF; }"
        )
        self._input.returnPressed.connect(self._on_send)
        self._input.installEventFilter(self)
        il.addWidget(self._input)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(56, 36)
        self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
        self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn)

        grip = QSizeGrip(self)
        grip.setStyleSheet("QSizeGrip { image: none; width: 14px; height: 14px; }")
        il.addWidget(grip)

        # Ollama 连接状态
        self._ollama_status = QLabel("●")
        self._ollama_status.setFixedWidth(16)
        self._ollama_status.setStyleSheet("font-size: 8px; color: #ccc; background: none;")
        self._ollama_status.setToolTip("检测中…")
        il.addWidget(self._ollama_status)

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
        """设置输入框文本并聚焦"""
        self._input.setText(text)
        self._input.setFocus()
        self._input.selectAll()

    def flash_busy(self) -> None:
        """短暂提示 worker 正忙，1.5 秒后恢复原 placeholder"""
        self._input.setPlaceholderText("⏳ 等待回复完成...")
        QTimer.singleShot(1500, lambda: self._input.setPlaceholderText("输入消息... (Enter 发送)"))

    def flash_export_btn(self) -> None:
        """导出按钮短暂显示 ✅ 表示已复制"""
        self._export_btn.setText("✅ 已复制")
        QTimer.singleShot(1500, lambda: self._export_btn.setText("📤 导出"))

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self.message_sent.emit(text)
        self._input.clear()

    def _check_ollama_status(self) -> None:
        """在后台线程探活 Ollama，避免阻塞 UI"""
        self._ping_worker = _OllamaPingWorker()
        self._ping_worker.result.connect(self._on_ollama_result)
        self._ping_worker.start()

    def _on_ollama_result(self, ok: bool) -> None:
        if ok:
            self._ollama_status.setStyleSheet("font-size: 8px; color: #4CAF50; background: none;")
            self._ollama_status.setToolTip("Ollama 已连接")
        else:
            self._ollama_status.setStyleSheet("font-size: 8px; color: #F44336; background: none;")
            self._ollama_status.setToolTip("Ollama 未连接")

    def add_user_message(self, text: str) -> None:
        bubble = self._make_bubble(text, is_user=True)
        self._insert_widget(bubble)

    def add_assistant_message(self, text: str) -> None:
        html = markdown.to_html(text)
        bubble = self._make_bubble(html, is_user=False, is_html=True)
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
            self._send_btn.setStyleSheet(
                "QPushButton { background: #e02020; color: white; border: none; border-radius: 5px;"
                "padding: 6px 14px; font-size: 12px; font-weight: bold; }"
                "QPushButton:hover { background: #c01010; }"
            )
            self._send_btn.clicked.connect(self.stop_requested.emit)
        else:
            self._send_btn.setText("发送")
            self._send_btn.setToolTip("")
            self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
            self._send_btn.clicked.connect(self._on_send)
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
            edit_btn.setObjectName("edit_btn_user")
            edit_btn.setCursor(Qt.PointingHandCursor)
            edit_btn.setVisible(False)
            edit_btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none; font-size: 10px; color: rgba(255,255,255,0.7);
                }
                QPushButton:hover {
                    color: white; background: rgba(255,255,255,0.15); border-radius: 3px;
                }
            """)
            edit_btn.clicked.connect(lambda checked, t=content: self.set_input_text(t))
            bl.addWidget(edit_btn)

            v_layout.addWidget(btn_bar)
            wl.addStretch()
            wl.addLayout(v_layout)

            wrapper.installEventFilter(self)
        else:
            lbl.setStyleSheet(
                "QLabel { background: #f0f0f0; color: #1a1a1a; border-radius: 10px;"
                "padding: 8px 12px; font-size: 13px; }"
            )
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
            copy_btn.setObjectName("copy_btn_assistant")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setVisible(False)
            copy_btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none; font-size: 11px; color: #999;
                }
                QPushButton:hover {
                    color: #333; background: rgba(0,0,0,0.06); border-radius: 3px;
                }
            """)
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
                if self._input.text():
                    self._input.clear()
                    return True
                return False  # 已空，放行 Esc 关闭对话框
            if (event.key() == Qt.Key_Return and
                    event.modifiers() == Qt.ShiftModifier):
                # QLineEdit 单行输入，Shift+Enter 暂不换行，仅阻止发送
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
            self.resize(440, 580)  # 仅首次弹出时使用默认尺寸
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
        self._ollama_timer.start()
        self._check_ollama_status()  # 打开时立即探活

    # ── 事件 ───────────────────────────────────────────

    def _in_title_bar(self, pos: QPoint) -> bool:
        return pos.y() <= 44

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._in_title_bar(event.pos()):
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:
        if self._auto_hide and event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event and event.key() == Qt.Key_Escape:
            if self._input.text():
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
