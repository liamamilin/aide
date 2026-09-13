"""
对话窗口 —— Agent 多轮对话
"""
import html
import logging
from pathlib import Path

from PyQt5.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeyEvent, QPainter, QPainterPath, QPixmap, QRegion, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ai_desktop import config
from ai_desktop.capture.text_normalizer import normalize
from ai_desktop.config import Agent
from ai_desktop.llm.service_checks import ImageCapability, ServiceState
from ai_desktop.services.action_service import Action
from ai_desktop.ui import markdown, styles, theme
from ai_desktop.ui.action_panel import ActionPanel
from ai_desktop.ui.float_button import pin_to_all_spaces
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.ui.ocr_preview_dialog import OCRPreviewDialog
from ai_desktop.utils import images as image_utils
from ai_desktop.utils.paths import resource_path
from ai_desktop.utils.window_state import (
    ScreenArea,
    WindowState,
    fit_window_state,
    parse_window_state,
    serialize_window_state,
)

logger = logging.getLogger(__name__)


def _rounded_pixmap(path: str, size: int, radius: float) -> QPixmap:
    """将应用图标裁成 Qt 界面里使用的圆角缩略图。"""
    source = QPixmap(path).scaled(
        size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
    )
    if source.isNull():
        return source
    result = QPixmap(size, size)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return result


class _ChatInputEdit(QPlainTextEdit):
    """输入框 —— 重写右键菜单以套用主题样式，并拦截粘贴/拖入的图片。"""

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu()
        menu.setStyleSheet(styles.menu_style())
        menu.exec_(event.globalPos())

    def insertFromMimeData(self, source) -> None:
        # 优先处理图片：粘贴剪贴板图片 / 拖入的图片文件
        if source.hasImage():
            img = source.imageData()
            pixmap = img if isinstance(img, QPixmap) else QPixmap.fromImage(img)
            if not pixmap.isNull() and hasattr(self, "_on_image_attach"):
                self._on_image_attach(pixmap)
            return
        urls = source.urls()
        if urls and hasattr(self, "_on_image_paths"):
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            if paths:
                self._on_image_paths(paths)
                return
        super().insertFromMimeData(source)


class _ClickableImage(QLabel):
    """可点击的图片 Label（点击回调图片路径）"""

    clicked = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._path)
        super().mousePressEvent(event)


class ChatDialog(FramelessDragMixin, QWidget):
    message_sent = pyqtSignal(str, list)     # (text, image_paths)
    screenshot_requested = pyqtSignal()
    new_convo_requested = pyqtSignal()
    history_requested = pyqtSignal()
    export_requested = pyqtSignal()
    manage_agents_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    agent_changed = pyqtSignal(Agent)
    model_changed = pyqtSignal(str)
    service_check_requested = pyqtSignal()
    action_requested = pyqtSignal(str, str, str)
    closed = pyqtSignal()
    geometry_changed = pyqtSignal()
    ocr_requested = pyqtSignal(str)
    ocr_cancel_requested = pyqtSignal()
    pending_images_changed = pyqtSignal(list)

    def __init__(self, agents: list[Agent], active_agent: Agent,
                 models: list[str] | None = None, active_model: str = "",
                 auto_hide: bool = False, parent=None,
                 actions: list[Action] | None = None,
                 actions_enabled: bool = True):
        super().__init__(parent)
        self._setup_drag(40)
        self._agents = agents
        self._active_agent = active_agent
        self._auto_hide = auto_hide
        self._auto_hide_suspended = False
        self._models = list(models) if models else []
        self._actions = list(actions or [])
        self._actions_enabled = actions_enabled
        self._action_has_conversation = False
        self._action_mode = "new"
        self._active_model = active_model
        self._image_capability = ImageCapability.UNKNOWN
        self._placement_initialized = False
        self._user_scrolled_up: bool = False
        self._pending_images: list[str] = []     # 发送前暂存的图片（应用数据目录路径）
        self._ocr_preview_dialog: OCRPreviewDialog | None = None
        self._stream_bubble: QLabel | None = None
        self._stream_copy_btn: QPushButton | None = None
        self._stream_text: str = ""
        self._stream_buffer: str = ""               # 积攒的回复 token
        self._thinking_text: str = ""               # 完整思考文本
        self._thinking_buffer: str = ""             # 积攒的思考 token
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(50)          # 50ms 刷新一次
        self._stream_timer.timeout.connect(self._flush_stream_buffer)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._apply_scroll_to_bottom)
        self._busy_feedback_timer = QTimer(self)
        self._busy_feedback_timer.setSingleShot(True)
        self._busy_feedback_timer.timeout.connect(self._reset_input_placeholder)
        self._export_feedback_timer = QTimer(self)
        self._export_feedback_timer.setSingleShot(True)
        self._export_feedback_timer.timeout.connect(self._reset_export_button)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._apply_auto_hide)
        self._ollama_timer = QTimer(self)
        self._ollama_timer.setInterval(30000)       # 每 30 秒探活
        self._ollama_timer.timeout.connect(self.service_check_requested.emit)
        # 输入历史浏览状态
        self._input_history: list[str] = []         # 最新在前
        self._hist_index = -1                       # -1 = 未在浏览
        self._hist_draft = ""                       # 进入浏览前保存的草稿
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
        self.setAcceptDrops(True)
        self._apply_rounded_mask()
        self.setStyleSheet(styles.CHAT_DIALOG_ROOT)

    def _apply_rounded_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_mask()
        self._update_bubble_widths()
        self.geometry_changed.emit()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.geometry_changed.emit()

    @staticmethod
    def _screen_areas() -> list[ScreenArea]:
        return [ScreenArea(screen.name(), screen.availableGeometry()) for screen in QApplication.screens()]

    def _screen_name(self) -> str:
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        return screen.name() if screen is not None else ""

    def geometry_state(self) -> str:
        return serialize_window_state(self.geometry(), self._screen_name(), include_size=True)

    def _apply_fitted_state(self, state: WindowState) -> bool:
        fitted = fit_window_state(
            state,
            self._screen_areas(),
            fallback_size=self.size(),
            minimum_size=QSize(400, 460),
        )
        if fitted is None:
            return False
        rect, screen = fitted
        self.setMinimumSize(min(400, screen.geometry.width()), min(460, screen.geometry.height()))
        self.setGeometry(rect)
        self._placement_initialized = True
        return True

    def restore_geometry(self, raw: str) -> bool:
        state = parse_window_state(raw, include_size=True)
        return state is not None and self._apply_fitted_state(state)

    def ensure_visible(self) -> None:
        state = WindowState(
            self.x(), self.y(), self.width(), self.height(), self._screen_name()
        )
        self._apply_fitted_state(state)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Row 1: 品牌、当前 Agent 与高频操作 ──
        title = QWidget()
        title.setFixedHeight(44)
        title.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)
        tl.setSpacing(7)

        icon_lbl = QLabel()
        icon_pixmap = _rounded_pixmap(
            resource_path("ai_desktop", "图标.png"), 26, 6
        )
        if not icon_pixmap.isNull():
            icon_lbl.setPixmap(icon_pixmap)
        icon_lbl.setStyleSheet(styles.TITLE_ICON)
        icon_lbl.setFixedSize(28, 28)
        tl.addWidget(icon_lbl)
        self._title_icon = icon_lbl

        name_lbl = QLabel("AI 桌面助手")
        name_lbl.setStyleSheet(styles.TITLE_NAME)
        tl.addWidget(name_lbl)
        self._title_name = name_lbl

        agent_lbl = QLabel(f"· {self._active_agent.name}")
        agent_lbl.setStyleSheet(styles.TITLE_AGENT)
        tl.addWidget(agent_lbl)
        self._title_agent = agent_lbl

        # 服务状态放在标题栏，避免与输入操作混在一起。
        self._ollama_dot = QWidget()
        self._ollama_dot.setFixedSize(8, 8)
        self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS)
        self._ollama_dot.setToolTip("检测中…")
        tl.addWidget(self._ollama_dot)

        tl.addStretch()

        self._new_convo_btn = QPushButton("＋ 新对话")
        self._new_convo_btn.setFixedHeight(28)
        self._new_convo_btn.setToolTip("开始一个新对话")
        self._new_convo_btn.setStyleSheet(styles.HEADER_ACTION_BUTTON)
        self._new_convo_btn.clicked.connect(self.new_convo_requested.emit)
        tl.addWidget(self._new_convo_btn)

        hide_btn = QPushButton("−")
        hide_btn.setFixedSize(24, 24)
        hide_btn.setStyleSheet(styles.ICON_BUTTON)
        hide_btn.clicked.connect(self.hide)
        tl.addWidget(hide_btn)

        root.addWidget(title)

        # ── Row 2: 当前任务配置；低频操作收入“更多” ──
        self._toolbar = QWidget()
        self._toolbar.setFixedHeight(42)
        self._toolbar.setStyleSheet(styles.TOOLBAR)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(10, 6, 10, 6)
        tb.setSpacing(7)

        # Agent 切换
        self._agent_combo = QComboBox()
        self._agent_combo.setFixedHeight(26)
        self._agent_combo.setFixedWidth(112)
        self._agent_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
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
        self._model_combo.setMinimumWidth(110)
        self._model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._model_combo.setStyleSheet(styles.MODEL_COMBO_BOX)
        self._model_combo.setToolTip("选择模型")
        if not self._models:
            self._model_combo.addItem("加载中…")
            self._model_combo.setEnabled(False)
        else:
            for m in self._models:
                self._model_combo.addItem(m)
            if self._active_model and self._active_model in self._models:
                self._model_combo.setCurrentText(self._active_model)
        self._model_combo.currentTextChanged.connect(self._on_model_combo)
        tb.addWidget(self._model_combo, stretch=1)

        self._model_capability_badge = QLabel("图片 ?")
        self._model_capability_badge.setObjectName("model_capability_badge")
        self._model_capability_badge.setStyleSheet(styles.STATUS_BADGE)
        self._model_capability_badge.setToolTip("当前模型的图片输入能力尚未确认")
        tb.addWidget(self._model_capability_badge)

        self._model_profile_badge = QLabel("全局")
        self._model_profile_badge.setObjectName("model_profile_badge")
        self._model_profile_badge.setStyleSheet(styles.STATUS_BADGE)
        self._model_profile_badge.setToolTip("请求将使用全局模型设置")
        tb.addWidget(self._model_profile_badge)

        more_btn = QPushButton("•••")
        more_btn.setFixedSize(30, 26)
        more_btn.setToolTip("更多操作")
        more_btn.setStyleSheet(styles.MORE_BUTTON)
        more_menu = QMenu(more_btn)
        more_menu.setStyleSheet(styles.menu_style())
        self._history_action = more_menu.addAction("对话历史…")
        self._export_action = more_menu.addAction("导出当前对话")
        more_menu.addSeparator()
        self._manage_agents_action = more_menu.addAction("管理 Agent…")
        self._history_action.triggered.connect(self.history_requested.emit)
        self._export_action.triggered.connect(self.export_requested.emit)
        self._manage_agents_action.triggered.connect(self.manage_agents_requested.emit)
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
        self._msg_layout.setContentsMargins(16, 12, 16, 10)
        self._msg_layout.setSpacing(8)
        self._empty_state = self._build_empty_state()
        self._msg_layout.addWidget(self._empty_state)
        self._msg_layout.addStretch()

        scroll.setWidget(self._msg_container)
        self._scroll = scroll
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        root.addWidget(scroll, stretch=1)

        self._action_panel = ActionPanel(self._actions, self)
        self._action_panel.action_selected.connect(self._on_action_selected)
        self._action_panel.cancelled.connect(self._focus_free_input)
        root.addWidget(self._action_panel)

        # ── 图片预览行（发送前暂存已附图片）──
        self._image_preview = QWidget()
        self._image_preview.setStyleSheet("background: transparent;")
        self._image_preview.setVisible(False)
        self._preview_layout = QHBoxLayout(self._image_preview)
        self._preview_layout.setContentsMargins(8, 4, 8, 4)
        self._preview_layout.setSpacing(6)
        root.addWidget(self._image_preview)

        # ── 输入区域 ──
        input_row = QHBoxLayout()
        input_row.setContentsMargins(8, 4, 8, 8)
        input_row.setSpacing(0)

        input_bar = QWidget()
        input_bar.setStyleSheet(styles.INPUT_BAR)
        il = QHBoxLayout(input_bar)
        il.setContentsMargins(8, 6, 8, 6)
        il.setSpacing(6)

        self._input = _ChatInputEdit()
        self._input.setPlaceholderText("输入消息 · Enter 发送 · Shift+Enter 换行")
        self._input.setToolTip("可直接粘贴或拖入图片")
        self._input.setFixedHeight(40)
        self._input.setStyleSheet(styles.INPUT_AREA)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._on_input_text_changed)
        self._input._on_image_attach = self._attach_pixmap
        self._input._on_image_paths = self._attach_image_paths

        action_btn = QPushButton("⚡")
        action_btn.setFixedSize(30, 40)
        action_btn.setStyleSheet(styles.ICON_BUTTON)
        action_btn.setToolTip("显示快捷动作")
        action_btn.clicked.connect(lambda: self.show_actions())
        self._action_btn = action_btn
        action_btn.setVisible(self._actions_enabled and bool(self._actions))
        il.addWidget(action_btn)

        # 图片附件按钮（📎 菜单：选择文件 / 截图 / 粘贴剪贴板图片）
        attach_btn = QPushButton("📎")
        attach_btn.setFixedSize(30, 40)
        attach_btn.setStyleSheet(styles.ICON_BUTTON)
        attach_btn.setToolTip("添加图片")
        self._attach_btn = attach_btn
        attach_menu = QMenu(attach_btn)
        attach_menu.setStyleSheet(styles.menu_style())
        a_file = attach_menu.addAction("选择图片文件…")
        a_shot = attach_menu.addAction("截图…")
        a_paste = attach_menu.addAction("粘贴剪贴板图片")
        a_file.triggered.connect(self._pick_image_files)
        a_shot.triggered.connect(self.screenshot_requested.emit)
        a_paste.triggered.connect(self._paste_clipboard_image)
        attach_btn.setMenu(attach_menu)
        il.addWidget(attach_btn)

        il.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(58, 40)
        self._send_btn.setStyleSheet(styles.BUTTON_PRIMARY)
        self._send_btn.clicked.connect(self._on_send)
        il.addWidget(self._send_btn)

        input_row.addWidget(input_bar, stretch=1)

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("QSizeGrip { image: none; }")
        input_row.addWidget(grip, alignment=Qt.AlignBottom | Qt.AlignRight)

        root.addLayout(input_row)

    def _build_empty_state(self) -> QWidget:
        """构建首屏引导，让用户在空对话中立即看懂三个核心入口。"""
        panel = QWidget()
        panel.setObjectName("chat_empty_state")
        panel.setStyleSheet(styles.EMPTY_CHAT_PANEL)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 44, 22, 24)
        layout.setSpacing(8)

        icon = QLabel()
        pixmap = QPixmap(resource_path("ai_desktop", "桌面宠物.png"))
        if not pixmap.isNull():
            icon.setPixmap(
                pixmap.scaled(74, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background: transparent;")
        layout.addWidget(icon)

        heading = QLabel("今天想处理什么？")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet(styles.EMPTY_CHAT_TITLE)
        layout.addWidget(heading)

        description = QLabel("直接输入问题，或从其他应用捕获文字和截图")
        description.setAlignment(Qt.AlignCenter)
        description.setWordWrap(True)
        description.setStyleSheet(styles.EMPTY_CHAT_DESCRIPTION)
        layout.addWidget(description)

        shortcuts = QLabel("⌘⌃L  选中文字     ⌘⌃S  截图     ⚡  快捷动作")
        shortcuts.setAlignment(Qt.AlignCenter)
        shortcuts.setWordWrap(True)
        shortcuts.setStyleSheet(styles.SHORTCUT_HINT)
        layout.addWidget(shortcuts)
        return panel

    def _update_bubble_widths(self) -> None:
        """让气泡随窗口缩放，同时保留左右对话层级。"""
        if not hasattr(self, "_msg_container"):
            return
        width = max(280, min(440, int(self.width() * 0.78)))
        for label in self._msg_container.findChildren(QLabel, "message_bubble"):
            label.setMaximumWidth(width)

    # ── Agent 切换 ─────────────────────────────────────

    def _on_agent_combo(self, index: int) -> None:
        agent_id = self._agent_combo.itemData(index)
        agent = next(ag for ag in self._agents if ag.id == agent_id)
        self._active_agent = agent
        self._title_agent.setText(f"· {agent.name}")
        self.agent_changed.emit(agent)

    def _on_model_combo(self, text: str) -> None:
        self._active_model = text
        self.model_changed.emit(text)

    @property
    def active_model(self) -> str:
        return self._active_model

    def set_cached_models(self, models: list[str], active_model: str) -> None:
        """Replace entries for a service address without claiming they are current."""
        cached = list(dict.fromkeys(models))
        display = list(cached)
        if active_model and active_model not in display:
            display.insert(0, active_model)
        self._models = cached
        self._active_model = active_model
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if display:
            self._model_combo.addItems(display)
            self._model_combo.setCurrentText(active_model or display[0])
            self._model_combo.setEnabled(True)
            if cached:
                self._model_combo.setToolTip("缓存模型；正在验证服务状态")
            else:
                self._model_combo.setToolTip("当前模型尚未通过服务验证")
        else:
            self._model_combo.addItem("加载中…")
            self._model_combo.setEnabled(False)
            self._model_combo.setToolTip("正在加载模型列表")
        self._model_combo.blockSignals(False)

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
            self._model_combo.setToolTip("选择模型")
        else:
            self._model_combo.setCurrentText(self._models[0])
            changed = True
            if current and current != "加载中…":
                self._model_combo.setToolTip(
                    f"模型 {current} 已不可用，已切换到 {self._models[0]}"
                )
            else:
                self._model_combo.setToolTip("选择模型")
        self._active_model = self._model_combo.currentText()
        self._model_combo.blockSignals(False)
        if changed:
            self.model_changed.emit(self._active_model)

    def set_active_agent(self, agent: Agent) -> None:
        self._active_agent = agent
        self._title_agent.setText(f"· {agent.name}")
        idx = next(i for i, ag in enumerate(self._agents) if ag.id == agent.id)
        self._agent_combo.blockSignals(True)
        self._agent_combo.setCurrentIndex(idx)
        self._agent_combo.blockSignals(False)

    def set_model_profile_summary(self, profile_name: str, summary: str,
                                  warnings: tuple[str, ...] = ()) -> None:
        """Show the effective request settings before submission."""
        self._model_profile_badge.setText(profile_name or "全局")
        tooltip = summary
        if warnings:
            tooltip += "\n" + "\n".join(f"⚠ {warning}" for warning in warnings)
        self._model_profile_badge.setToolTip(tooltip)

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
        if not enabled:
            self._auto_hide_timer.stop()

    def refresh_theme(self) -> None:
        """Re-render theme-dependent rich text without changing message state."""
        for label in self._msg_container.findChildren(QLabel):
            source = getattr(label, "_markdown_source", None)
            if source is None:
                continue
            thinking = getattr(label, "_thinking_source", "")
            body, code_map = self._render_assistant_body(source, thinking)
            label.setText(self._wrap_assistant_html(body))
            label.code_map = code_map
        self.update()

    # ── 输入 ───────────────────────────────────────────

    def set_input_text(self, text: str) -> None:
        self._input.setPlainText(text)
        self._input.setFocus()
        self._input.selectAll()

    def show_actions(self, material: str | None = None,
                     selected_id: str | None = None) -> None:
        if not self._actions_enabled or not self._actions:
            return
        if material is not None:
            self.set_input_text(material)
        self._action_panel.show_for_material(
            self._input.toPlainText(),
            selected_id,
            has_conversation=self._action_has_conversation,
            mode=self._action_mode,
        )

    def refresh_actions(self, actions: list[Action]) -> None:
        self._actions = list(actions)
        self._action_panel.refresh_actions(self._actions)
        self._action_btn.setVisible(self._actions_enabled and bool(self._actions))
        if not self._actions:
            self._action_panel.hide()

    def set_action_context(self, has_conversation: bool, mode: str = "new") -> None:
        self._action_has_conversation = has_conversation
        self._action_mode = mode if mode in {"new", "current"} else "new"

    def set_actions_enabled(self, enabled: bool) -> None:
        self._actions_enabled = bool(enabled)
        self._action_btn.setVisible(self._actions_enabled and bool(self._actions))
        if not self._actions_enabled:
            self._action_panel.hide()

    def _on_action_selected(self, action_id: str, material: str, mode: str) -> None:
        self._input.clear()
        self._exit_input_browsing()
        self._input.setFocus(Qt.ShortcutFocusReason)
        self._action_mode = mode
        self.action_requested.emit(action_id, material, mode)

    def _focus_free_input(self) -> None:
        self._input.setFocus(Qt.ShortcutFocusReason)

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
        self._busy_feedback_timer.start(1500)

    def _reset_input_placeholder(self) -> None:
        self._input.setPlaceholderText(
            "输入消息 · Enter 发送 · Shift+Enter 换行"
        )

    def flash_export_btn(self) -> None:
        self._export_action.setText("✓ 已复制到剪贴板")
        self._export_feedback_timer.start(1500)

    def _reset_export_button(self) -> None:
        self._export_action.setText("导出当前对话")

    def _on_send(self) -> None:
        text = normalize(self._input.toPlainText())
        images = list(self._pending_images)
        if not text and not images:
            return
        self._action_panel.hide()
        # Clear before the synchronous signal. A rejected submission restored
        # by the controller must remain after this method returns.
        self._input.clear()
        self._exit_input_browsing()
        self._pending_images = []
        self._refresh_image_preview()
        self.message_sent.emit(text, images)

    # ── 图片附件 ───────────────────────────────────────

    def attach_image_paths(self, paths: list[str]) -> None:
        """外部（截图/历史恢复）传入图片文件路径：复制到应用数据目录并加入待发列表。"""
        errors: list[str] = []
        for p in paths:
            if len(self._pending_images) >= image_utils.MAX_ATTACHMENTS_PER_MESSAGE:
                errors.append(
                    f"每条消息最多添加 {image_utils.MAX_ATTACHMENTS_PER_MESSAGE} 张图片。"
                )
                break
            try:
                if not p or not image_utils.is_image_file(p):
                    if p:
                        errors.append(f"{Path(p).name}：格式不受支持。")
                    continue
                stored = image_utils.store_image(p)
                if stored not in self._pending_images:
                    self._pending_images.append(stored)
            except (image_utils.AttachmentError, FileNotFoundError) as exc:
                errors.append(f"{Path(p).name}：{exc}")
            except Exception:
                logger.exception("Failed to attach image %s", p)
                errors.append(f"{Path(p).name}：读取失败，请重试。")
        self._refresh_image_preview()
        if errors:
            self._show_attachment_errors(errors)

    def restore_draft(self, text: str, image_paths: list[str]) -> None:
        """Restore paths already stored by this dialog without copying again."""
        self.set_input_text(text)
        self._pending_images = [
            path for path in image_paths
            if path and image_utils.is_image_file(path) and Path(path).is_file()
        ]
        self._refresh_image_preview()

    def _attach_image_paths(self, paths: list[str]) -> None:
        self.attach_image_paths(paths)

    def _attach_pixmap(self, pixmap) -> None:
        if len(self._pending_images) >= image_utils.MAX_ATTACHMENTS_PER_MESSAGE:
            self._show_attachment_errors(
                [f"每条消息最多添加 {image_utils.MAX_ATTACHMENTS_PER_MESSAGE} 张图片。"]
            )
            return
        try:
            if pixmap.isNull():
                return
            stored = image_utils.store_pixmap(pixmap)
            if stored not in self._pending_images:
                self._pending_images.append(stored)
            self._refresh_image_preview()
        except image_utils.AttachmentError as exc:
            self._show_attachment_errors([str(exc)])
        except Exception:
            logger.exception("Failed to attach pasted pixmap")
            self._show_attachment_errors(["剪贴板图片读取失败，请重试。"])

    def _show_attachment_errors(self, errors: list[str]) -> None:
        unique = list(dict.fromkeys(errors))
        QMessageBox.warning(self, "无法添加图片", "\n".join(unique))

    def _pick_image_files(self) -> None:
        was_visible = self.isVisible()
        self._auto_hide_suspended = True
        self._auto_hide_timer.stop()
        try:
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择图片", "",
                "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tiff *.heic);;所有文件 (*)",
            )
        finally:
            # macOS uses a native panel that Qt does not always report as an
            # owned active window. Keep auto-hide from dismissing the chat
            # while that panel is open, then return focus to the draft.
            self._auto_hide_timer.stop()
            if was_visible:
                self.show()
                self.activateWindow()
                self.raise_()
            self._auto_hide_suspended = False
        if files:
            self.attach_image_paths(files)

    def _paste_clipboard_image(self) -> None:
        img = QApplication.clipboard().image()
        if img.isNull():
            return
        pix = QPixmap.fromImage(img)
        self._attach_pixmap(pix)

    def clear_pending_images(self) -> None:
        for path in self._pending_images:
            try:
                image_utils.discard_staged_image(path)
            except Exception:
                logger.warning("Failed to discard draft image %s", path, exc_info=True)
        self._pending_images = []
        self._refresh_image_preview()

    def _refresh_image_preview(self) -> None:
        self.pending_images_changed.emit(list(self._pending_images))
        layout = self._preview_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._pending_images:
            self._image_preview.setVisible(False)
            return
        for idx, path in enumerate(self._pending_images):
            item = QWidget()
            item.setFixedSize(68, 68)
            il = QGridLayout(item)
            il.setContentsMargins(2, 2, 2, 2)
            thumb = QLabel()
            thumb.setFixedSize(64, 64)
            pix = QPixmap(path)
            if not pix.isNull():
                thumb.setPixmap(
                    pix.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            try:
                info = image_utils.inspect_image(path, enforce_limits=False)
                tooltip = (
                    f"{info.original_name}\n{info.width} × {info.height}\n"
                    f"{info.byte_size / (1024 * 1024):.1f} MiB"
                )
                if info.needs_inference_resize:
                    tooltip += "\n发送时生成最长边 2048 像素的推理副本"
                thumb.setToolTip(tooltip)
            except Exception:
                thumb.setToolTip(Path(path).name)
            thumb.setStyleSheet("border-radius: 4px;")
            il.addWidget(thumb, 0, 0)
            ocr = QPushButton("识字")
            ocr.setObjectName("ocr_image_btn")
            ocr.setFixedSize(44, 20)
            ocr.setToolTip("在本机提取这张图片中的文字")
            ocr.setStyleSheet(styles.SECONDARY_BUTTON)
            ocr.clicked.connect(
                lambda checked=False, image_path=path: self.ocr_requested.emit(image_path)
            )
            il.addWidget(ocr, 0, 0, alignment=Qt.AlignBottom | Qt.AlignLeft)
            rm = QPushButton("✕")
            rm.setFixedSize(16, 16)
            rm.setStyleSheet(
                "QPushButton { background: rgba(0,0,0,0.6); color: white; border: none;"
                " border-radius: 8px; font-size: 9px; }"
                "QPushButton:hover { background: #ff3b30; }"
            )
            rm.clicked.connect(lambda checked, i=idx: self._remove_pending_image(i))
            il.addWidget(rm, 0, 0, alignment=Qt.AlignTop | Qt.AlignRight)
            layout.addWidget(item)
        self._image_preview.setVisible(True)

    def show_ocr_loading(self, image_path: str) -> None:
        preview = self._ensure_ocr_preview()
        preview.begin(image_path)

    def show_ocr_result(
        self,
        image_path: str,
        text: str,
        *,
        block_count: int,
        elapsed_ms: float,
        languages: tuple[str, ...],
        low_confidence: bool,
    ) -> None:
        preview = self._ensure_ocr_preview()
        if preview.image_path != image_path:
            return
        preview.show_result(
            text,
            block_count=block_count,
            elapsed_ms=elapsed_ms,
            languages=languages,
            low_confidence=low_confidence,
        )

    def show_ocr_empty(self, image_path: str, elapsed_ms: float) -> None:
        preview = self._ensure_ocr_preview()
        if preview.image_path == image_path:
            preview.show_empty(elapsed_ms)

    def show_ocr_error(self, image_path: str, error: str) -> None:
        preview = self._ensure_ocr_preview()
        if preview.image_path == image_path:
            preview.show_error(error)

    def close_ocr_preview(self, image_path: str | None = None) -> None:
        preview = self._ocr_preview_dialog
        if preview is None:
            return
        if image_path is None or preview.image_path == image_path:
            preview.close()

    def _ensure_ocr_preview(self) -> OCRPreviewDialog:
        if self._ocr_preview_dialog is None:
            preview = OCRPreviewDialog(self)
            preview.text_accepted.connect(self._use_ocr_text)
            preview.cancel_requested.connect(self.ocr_cancel_requested.emit)
            self._ocr_preview_dialog = preview
        return self._ocr_preview_dialog

    def _insert_ocr_text(self, text: str) -> None:
        cursor = self._input.textCursor()
        if self._input.toPlainText() and cursor.position() > 0:
            cursor.insertText("\n")
        cursor.insertText(text)
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def _use_ocr_text(
        self,
        text: str,
        image_path: str,
        keep_image: bool,
    ) -> None:
        self._insert_ocr_text(text)
        if keep_image or image_path not in self._pending_images:
            return
        self._pending_images.remove(image_path)
        try:
            image_utils.discard_staged_image(image_path)
        except Exception:
            logger.warning(
                "Failed to discard OCR source image %s",
                image_path,
                exc_info=True,
            )
        self._refresh_image_preview()

    def _remove_pending_image(self, idx: int) -> None:
        if 0 <= idx < len(self._pending_images):
            path = self._pending_images.pop(idx)
            try:
                image_utils.discard_staged_image(path)
            except Exception:
                logger.warning("Failed to discard draft image %s", path, exc_info=True)
            self._refresh_image_preview()

    def get_pending_images(self) -> list[str]:
        return list(self._pending_images)

    # ── 拖拽图片文件到对话窗口 ─────────────────────────

    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            self.attach_image_paths(paths)
            event.acceptProposedAction()
        elif md.hasImage():
            img = md.imageData()
            pix = img if isinstance(img, QPixmap) else QPixmap.fromImage(img)
            self._attach_pixmap(pix)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _adjust_input_height(self) -> None:
        doc = self._input.document()
        total_lines = 0
        block = doc.begin()
        while block.isValid():
            total_lines += block.layout().lineCount()
            block = block.next()
        line_h = self._input.fontMetrics().lineSpacing()
        h = total_lines * line_h + 18  # padding(8+8) + border(1+1)
        new_h = max(40, min(120, h))
        cur_h = self._input.height()
        if cur_h != new_h:
            self._input.setFixedHeight(new_h)

    def take_shutdown_workers(self) -> list:
        """Stop timers owned by the window during app shutdown."""
        self._ollama_timer.stop()
        self._stream_timer.stop()
        self._scroll_timer.stop()
        self._busy_feedback_timer.stop()
        self._export_feedback_timer.stop()
        self._auto_hide_timer.stop()
        self.close_ocr_preview()
        self.clear_pending_images()
        return []

    def set_service_status(self, state: ServiceState) -> None:
        """Render service reachability independently from cached model entries."""
        if state == ServiceState.ONLINE:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_OK)
            self._ollama_dot.setToolTip("Ollama 已连接，模型列表已更新")
        elif state == ServiceState.EMPTY:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_WARN)
            self._ollama_dot.setToolTip("Ollama 已连接，但没有可用模型")
        elif state == ServiceState.INVALID:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_WARN)
            self._ollama_dot.setToolTip("Ollama 响应格式错误")
        elif state == ServiceState.OFFLINE:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS_ERR)
            self._ollama_dot.setToolTip("Ollama 未连接；模型列表可能来自缓存")
        else:
            self._ollama_dot.setStyleSheet(styles.OLLAMA_STATUS)
            self._ollama_dot.setToolTip("正在检测 Ollama…")

    def set_image_capability(
        self,
        capability: ImageCapability,
        *,
        checking: bool = False,
        cached: bool = False,
    ) -> None:
        """Show the selected model's declared image-input capability."""
        self._image_capability = capability
        if checking:
            text = "图片 …"
            detail = "正在检查当前模型的图片输入能力"
        elif capability == ImageCapability.SUPPORTED:
            text = "图片 ✓"
            detail = "当前模型已声明支持图片输入"
        elif capability == ImageCapability.UNSUPPORTED:
            text = "图片 ×"
            detail = "当前模型已声明不支持图片输入"
        else:
            text = "图片 ?"
            detail = "当前模型的图片输入能力尚未确认"
        if cached and not checking:
            detail += "（缓存结果）"
        self._model_capability_badge.setText(text)
        self._model_capability_badge.setToolTip(detail)
        self._attach_btn.setToolTip(f"添加图片\n{detail}")

    def confirm_unknown_image_capability(self, model: str) -> bool:
        reply = QMessageBox.question(
            self,
            "图片能力尚未确认",
            f"尚未确认模型 {model} 是否支持图片输入。\n仍要尝试发送吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def focus_model_selector(self) -> None:
        self._model_combo.setFocus()

    def add_user_message(
        self,
        text: str,
        images: list[str] | None = None,
        missing_images: list[str] | None = None,
    ) -> None:
        bubble = self._make_bubble(
            text,
            is_user=True,
            images=images,
            missing_images=missing_images,
        )
        self._insert_widget(bubble)

    def add_assistant_message(self, text: str) -> None:
        body, code_map = self._render_assistant_body(text)
        bubble = self._make_bubble(
            body,
            is_user=False,
            is_html=True,
            code_map=code_map,
            markdown_source=text,
        )
        btn = bubble.findChild(QPushButton, "copy_btn_assistant")
        if btn:
            btn.clicked.connect(lambda checked, t=text: self._copy_to_clipboard(t))
        self._insert_widget(bubble)

    @staticmethod
    def _wrap_assistant_html(body: str) -> str:
        return (
            '<html><body style="font-size:13px; font-family:'
            + config.FONT_FAMILY
            + ';">'
            + body
            + "</body></html>"
        )

    @staticmethod
    def _render_assistant_body(
        text: str, thinking: str = "",
    ) -> tuple[str, dict[str, str]]:
        body, code_map = markdown.to_html(text)
        if thinking.strip():
            colors = theme.current()
            escaped = html.escape(thinking, quote=False)
            body = (
                f'<details style="margin-bottom:10px;color:{colors.text_secondary};'
                'font-size:12px;">'
                f'<summary style="cursor:pointer;color:{colors.text_secondary};">'
                "💭 思考过程</summary>"
                '<pre style="white-space:pre-wrap;word-break:break-word;'
                f'margin-top:4px;color:{colors.text};">{escaped}</pre>'
                "</details>"
                + body
            )
        return body, code_map

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

    def finalize_assistant_stream(self, text: str, ok: bool, *, error: str = "", cancelled: bool = False) -> None:
        """流式结束，刷新残留并转为 Markdown HTML"""
        self._stream_timer.stop()
        self._flush_stream_buffer()
        if self._stream_bubble is None:
            return
        # 完成结果是权威正文；错误和取消提示不混入正文或后续推理上下文。
        self._stream_text = text
        if ok and self._stream_text:
            body, code_map = self._render_assistant_body(
                self._stream_text, self._thinking_text,
            )
            self._stream_bubble.setText(self._wrap_assistant_html(body))
            self._stream_bubble.setTextFormat(Qt.RichText)
            self._stream_bubble._markdown_source = self._stream_text
            self._stream_bubble._thinking_source = self._thinking_text
            if code_map:
                self._stream_bubble.code_map = code_map
                self._stream_bubble.linkActivated.connect(self._on_link_activated)
        elif not ok:
            status = "⏹ 已停止生成（未完成）" if cancelled else f"❌ {error or '生成失败，请重试。'}"
            display = f"{text}\n\n{status}" if text else status
            self._stream_bubble.setText(display)
            self._stream_bubble.setTextFormat(Qt.PlainText)

        # 连接复制按钮
        if self._stream_copy_btn:
            copy_text = self._stream_text or error
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
        self._stream_timer.stop()
        self._stream_bubble = None
        self._stream_copy_btn = None
        self._stream_text = ""
        self._stream_buffer = ""
        self._thinking_text = ""
        self._thinking_buffer = ""
        while self._msg_layout.count() > 2:  # keep empty state + stretch
            item = self._msg_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._empty_state.show()
        self.clear_pending_images()

    # ── 气泡 ───────────────────────────────────────────

    def _make_bubble(
        self, content: str, is_user: bool, is_html: bool = False,
        code_map: dict[str, str] | None = None,
        images: list[str] | None = None,
        missing_images: list[str] | None = None,
        markdown_source: str | None = None,
    ) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel()
        lbl.setObjectName("message_bubble")
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(max(280, min(440, int(self.width() * 0.78))))
        lbl.setTextFormat(Qt.RichText if is_html else Qt.PlainText)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

        if is_html and code_map:
            lbl.linkActivated.connect(self._on_link_activated)
            lbl.code_map = code_map
        if markdown_source is not None:
            lbl._markdown_source = markdown_source
            lbl._thinking_source = ""

        if is_user:
            lbl.setStyleSheet(styles.USER_BUBBLE)
            # 用户气泡 + 编辑按钮（hover 显示）
            v_layout = QVBoxLayout()
            v_layout.setContentsMargins(0, 0, 0, 0)
            v_layout.setSpacing(2)
            if images or missing_images:
                v_layout.addWidget(
                    self._build_bubble_images(images or [], missing_images or [])
                )
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
            lbl.setText(self._wrap_assistant_html(content))
        else:
            lbl.setText(content)

        return wrapper

    def _build_bubble_images(
        self, images: list[str], missing_images: list[str] | None = None,
    ) -> QWidget:
        """构建气泡内图片展示区（缩略横排，点击可放大查看）"""
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        for path in images:
            thumb = _ClickableImage(path)
            pix = QPixmap(path)
            if pix.isNull():
                continue
            thumb.setPixmap(
                pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            thumb.setStyleSheet(
                "border-radius: 6px; border: 1px solid rgba(255,255,255,0.25);"
            )
            thumb.clicked.connect(self._view_image_full)
            bl.addWidget(thumb, alignment=Qt.AlignLeft)
        for path in missing_images or []:
            missing = QLabel(f"⚠️ 图片缺失\n{Path(path).name}")
            missing.setObjectName("missing_image_notice")
            missing.setToolTip(path)
            missing.setStyleSheet(
                "padding: 8px; border-radius: 6px; "
                "border: 1px dashed rgba(255,170,0,0.75); color: #b7791f;"
            )
            bl.addWidget(missing, alignment=Qt.AlignLeft)
        return box

    def _view_image_full(self, path: str) -> None:
        """在新窗口预览大图（查看图片详情）"""
        try:
            from PyQt5.QtWidgets import QDialog, QScrollArea
            dlg = QDialog(self)
            dlg.setWindowTitle("图片预览")
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
            dlg.setMinimumSize(300, 300)
            label = QLabel()
            pix = QPixmap(path)
            if pix.isNull():
                return
            label.setPixmap(pix)
            scroll = QScrollArea()
            scroll.setWidget(label)
            lay = QVBoxLayout(dlg)
            lay.addWidget(scroll)
            dlg.resize(min(pix.width(), 900) + 40, min(pix.height(), 900) + 40)
            dlg.exec_()
        except Exception:
            pass

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
        if self._action_panel.isVisible():
            self._action_panel.set_material(self._input.toPlainText())

    # ── 插入气泡 ───────────────────────────────────────

    def _insert_widget(self, w: QWidget) -> None:
        self._empty_state.hide()
        idx = self._msg_layout.count() - 1
        self._msg_layout.insertWidget(idx, w)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        # Wait for layout without recursively processing worker/user signals.
        self._scroll_timer.start(0)

    def _apply_scroll_to_bottom(self) -> None:
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
        if not self._placement_initialized:
            w, h = self._default_size()
            x = anchor.x() - w - 12
            y = anchor.y() - h // 2
            screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                if x < geo.left():
                    x = anchor.x() + 60
                if y < geo.top():
                    y = geo.top() + 8
                if y + h > geo.y() + geo.height():
                    y = geo.y() + geo.height() - h - 8
                self._apply_fitted_state(WindowState(x, y, w, h, screen.name()))
            else:
                self.resize(w, h)
                self.move(x, y)
                self._placement_initialized = True
        else:
            self.ensure_visible()
        self.show()
        pin_to_all_spaces(self)
        self.activateWindow()
        self.raise_()
        self._input.setFocus()
        # 重新打开时滚动到底部显示最新消息
        self._user_scrolled_up = False
        self._scroll_to_bottom()
        self._ollama_timer.start()
        self.service_check_requested.emit()  # 打开时立即探活

    # ── 事件 ───────────────────────────────────────────

    def _on_link_activated(self, url: str) -> None:
        lbl = self.sender()
        code_map = getattr(lbl, "code_map", {})
        code = code_map.get(url, "")
        if code:
            self._copy_to_clipboard(code)

    def _owns_window(self, candidate: QWidget | None) -> bool:
        widget = candidate
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _has_active_owned_window(self) -> bool:
        """Return whether focus moved to a child window owned by this dialog."""
        return any(
            self._owns_window(candidate)
            for candidate in (QApplication.activeModalWidget(), QApplication.activeWindow())
        )

    def _apply_auto_hide(self) -> None:
        if (
            self._auto_hide
            and not self._auto_hide_suspended
            and self.isVisible()
            and not self.isActiveWindow()
            and not self._has_active_owned_window()
        ):
            self.hide()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.ActivationChange:
            if (
                self._auto_hide_suspended
                or self.isActiveWindow()
                or not self._auto_hide
            ):
                self._auto_hide_timer.stop()
            else:
                # Active window ownership is only reliable after Qt completes
                # the activation transition (for example opening HistoryDialog).
                self._auto_hide_timer.start(0)
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
