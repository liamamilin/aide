"""桌面宠物入口：可拖拽、跨 Spaces，并显示捕获与生成状态。"""
import ctypes
import ctypes.util
import math
import os
import sys

from PyQt5.QtCore import QPoint, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton

from ai_desktop.ui import styles
from ai_desktop.utils.paths import resource_path
from ai_desktop.utils.window_state import (
    ScreenArea,
    WindowState,
    fit_window_state,
    parse_window_state,
    serialize_window_state,
)

_ICON_PATH = next(
    (resource_path("ai_desktop", f) for f in ("图标.icns", "图标.png")
     if os.path.exists(resource_path("ai_desktop", f))),
    resource_path("ai_desktop", "图标.png"),
)
_PET_PATH = resource_path("ai_desktop", "桌面宠物.png")

_COMPACT_SIZE = 44
_PET_SIZE = QSize(116, 122)


def _make_circular_icon(path: str, size: int) -> QIcon:
    """加载图片并裁剪为圆形，返回 QIcon"""
    source = QPixmap(path).scaled(
        size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
    )
    w, h = source.width(), source.height()
    side = min(w, h)
    x = (w - side) // 2
    y = (h - side) // 2
    cropped = source.copy(x, y, side, side)

    result = QPixmap(side, side)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, side, side))
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return QIcon(result)


def pin_to_all_spaces(widget) -> None:
    """设置 NSWindow collection behavior，使窗口出现在所有 macOS Spaces 上"""
    app = QApplication.instance()
    if sys.platform != "darwin" or app is None or app.platformName() != "cocoa":
        return
    try:
        lib_path = ctypes.util.find_library("objc")
        if not lib_path:
            return
        objc = ctypes.cdll.LoadLibrary(lib_path)

        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        view_ptr = ctypes.c_void_p(int(widget.winId()))
        sel_window = objc.sel_registerName(b"window")
        sel_behavior = objc.sel_registerName(b"setCollectionBehavior:")

        SendId = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        SendBeh = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)

        msg_send_id = SendId(("objc_msgSend", objc))
        msg_send_beh = SendBeh(("objc_msgSend", objc))

        ns_win = msg_send_id(view_ptr, sel_window)
        if ns_win:
            msg_send_beh(ns_win, sel_behavior, 1)
    except Exception:
        pass


class FloatButton(QPushButton):
    exit_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    about_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    auto_hide_toggled = pyqtSignal(bool)
    pet_mode_toggled = pyqtSignal(bool)
    quick_action_requested = pyqtSignal(str)
    placement_changed = pyqtSignal()

    def __init__(self, parent=None, *, pet_enabled: bool = True):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._auto_hide = False
        self._is_dragging: bool = False
        self._pet_enabled = bool(pet_enabled and os.path.exists(_PET_PATH))
        self._listening = False
        self._responding: bool = False
        self._result_state: str | None = None
        self._hovered = False
        self._quick_actions: list[tuple[str, str]] = []
        self._animation_phase = 0
        self._pet_source = QPixmap(_PET_PATH) if os.path.exists(_PET_PATH) else QPixmap()
        self._pet_content = self._cropped_pet(self._pet_source)
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(180)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._result_timer = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.timeout.connect(self._clear_result_state)
        self._init_ui()
        self._position_initial()
        self._start_screen_tracking()

    def _init_ui(self) -> None:
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("AI 桌面宠物")
        self._apply_mode()

    @staticmethod
    def _cropped_pet(source: QPixmap) -> QPixmap:
        if source.isNull():
            return source
        bounds = QRegion(source.mask()).boundingRect()
        if bounds.isNull():
            return source
        margin = max(8, int(min(bounds.width(), bounds.height()) * 0.035))
        return source.copy(bounds.adjusted(-margin, -margin, margin, margin).intersected(source.rect()))

    def _apply_mode(self) -> None:
        if self._pet_enabled and not self._pet_content.isNull():
            self.setFixedSize(_PET_SIZE)
            self.setIcon(QIcon())
            self.setText("")
            self.setStyleSheet("QPushButton { background: transparent; border: none; }")
            self.setToolTip(self._state_tooltip())
            if self.isVisible():
                self._animation_timer.start()
            self.update()
            return

        self._pet_enabled = False
        self._animation_timer.stop()
        self.setFixedSize(_COMPACT_SIZE, _COMPACT_SIZE)

        if os.path.exists(_ICON_PATH):
            icon = _make_circular_icon(_ICON_PATH, _COMPACT_SIZE)
            self.setIcon(icon)
            self.setIconSize(self.size())
            self.setStyleSheet(
                "QPushButton { background: transparent; border: none; "
                f"border-radius: {_COMPACT_SIZE // 2}px; }}"
                "QPushButton:hover { background: rgba(255, 255, 255, 0.15); "
                f"border-radius: {_COMPACT_SIZE // 2}px; }}"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: rgba(0,122,255,0.85); "
                f"border-radius: {_COMPACT_SIZE // 2}px; }}"
            )
            self.setText("AI")
        self.setToolTip("AI 桌面助手")
        if self._responding and self.isVisible():
            self._animation_timer.start()

    def set_pet_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled and not self._pet_content.isNull())
        if enabled == self._pet_enabled:
            return
        anchor = self.geometry().bottomRight()
        self._pet_enabled = enabled
        self._apply_mode()
        self.move(anchor.x() - self.width() + 1, anchor.y() - self.height() + 1)
        self.ensure_visible()

    @property
    def pet_enabled(self) -> bool:
        return self._pet_enabled

    def set_quick_actions(self, actions: list[tuple[str, str]]) -> None:
        """Set up to three recent text actions shown in the pet context menu."""
        cleaned: list[tuple[str, str]] = []
        seen: set[str] = set()
        for action_id, name in actions:
            action_id = str(action_id).strip()
            name = str(name).strip()
            if action_id and name and action_id not in seen:
                cleaned.append((action_id, name))
                seen.add(action_id)
            if len(cleaned) == 3:
                break
        self._quick_actions = cleaned

    def paintEvent(self, event) -> None:
        if not self._pet_enabled or self._pet_content.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        bob = round(math.sin(self._animation_phase * math.pi / 6) * 1.5)
        extra = 2 if self._hovered else 0
        target = self.rect().adjusted(4 - extra, 5 + bob - extra, -4 + extra, -7 + extra)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(14, 24, 55, 38))
        painter.drawEllipse(24, self.height() - 10, self.width() - 48, 6)
        painter.drawPixmap(target, self._pet_content)

        state = self._effective_state()
        if state != "idle":
            bubble_width = 38 if state == "working" else 30
            bubble = QRectF(self.width() - bubble_width - 3, 3, bubble_width, 24)
            painter.setPen(QPen(QColor(92, 207, 255, 210), 1.5))
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.drawRoundedRect(bubble, 12, 12)
            if state == "listening":
                painter.setPen(QPen(QColor(20, 142, 235, 230), 2.2))
                center_x = bubble.center().x()
                painter.drawLine(
                    round(center_x), round(bubble.top() + 6),
                    round(center_x), round(bubble.bottom() - 7),
                )
                painter.drawLine(
                    round(center_x), round(bubble.bottom() - 7),
                    round(center_x - 4), round(bubble.bottom() - 11),
                )
                painter.drawLine(
                    round(center_x), round(bubble.bottom() - 7),
                    round(center_x + 4), round(bubble.bottom() - 11),
                )
            elif state == "working":
                active_dot = (self._animation_phase // 2) % 3
                for index in range(3):
                    alpha = 235 if index == active_dot else 90
                    painter.setBrush(QColor(20, 142, 235, alpha))
                    x = bubble.left() + bubble.width() / 2 + (index - 1) * 7
                    painter.drawEllipse(QRectF(x - 2, bubble.center().y() - 2, 4, 4))
            elif state == "success":
                painter.setPen(QPen(QColor(27, 166, 93, 235), 2.3))
                center = bubble.center()
                painter.drawLine(
                    round(center.x() - 5), round(center.y()),
                    round(center.x() - 1), round(center.y() + 4),
                )
                painter.drawLine(
                    round(center.x() - 1), round(center.y() + 4),
                    round(center.x() + 6), round(center.y() - 5),
                )
            else:
                painter.setPen(QPen(QColor(218, 75, 87, 235), 2.3))
                center = bubble.center()
                painter.drawLine(
                    round(center.x()), round(center.y() - 6),
                    round(center.x()), round(center.y() + 2),
                )
                painter.setBrush(QColor(218, 75, 87, 235))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QRectF(center.x() - 1.5, center.y() + 5, 3, 3))
        painter.end()

    def _effective_state(self) -> str:
        if self._responding:
            return "working"
        if self._listening:
            return "listening"
        if self._result_state:
            return self._result_state
        return "idle"

    def _state_tooltip(self) -> str:
        return {
            "idle": "AI 桌面宠物 · 点击对话，拖动放置",
            "listening": "正在读取选中内容…",
            "working": "AI 正在回复…",
            "success": "回复已完成",
            "error": "这次没有完成，可点击查看",
        }[self._effective_state()]

    def _advance_animation(self) -> None:
        self._animation_phase = (self._animation_phase + 1) % 12
        if not self._pet_enabled:
            if self._responding:
                self.setWindowOpacity(0.55 if self._animation_phase < 6 else 1.0)
            return
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        if self._pet_enabled or self._responding:
            self._animation_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._animation_timer.stop()
        self._result_timer.stop()
        self._result_state = None
        super().hideEvent(event)

    # ── 响应状态脉冲 ───────────────────────────────────

    def set_responding(self, responding: bool) -> None:
        """设置 AI 生成状态。"""
        self._responding = responding
        if responding:
            self._result_timer.stop()
            self._result_state = None
            if self.isVisible():
                self._animation_timer.start()
        elif not self._pet_enabled:
            self._animation_timer.stop()
        self.setWindowOpacity(1.0)
        self.setToolTip(self._state_tooltip())
        self.update()

    def show_result(self, succeeded: bool) -> None:
        """短暂显示本次请求结果，然后恢复空闲状态。"""
        self._result_state = "success" if succeeded else "error"
        self._result_timer.start(1600)
        self.setToolTip(self._state_tooltip())
        self.update()

    def _clear_result_state(self) -> None:
        self._result_state = None
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_listening(self, listening: bool) -> None:
        """设置选区或截图捕获状态；生成状态始终优先。"""
        self._listening = listening
        self.setToolTip(self._state_tooltip())
        self.update()

    # ── 屏幕跟随 ───────────────────────────────────────

    def _start_screen_tracking(self) -> None:
        """定时检测鼠标所在屏幕，自动跟随"""
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(500)
        self._track_timer.timeout.connect(self._follow_cursor_screen)
        self._track_timer.start()

    def _follow_cursor_screen(self) -> None:
        """如果鼠标所在的屏幕与按钮不同，移动按钮到鼠标所在屏幕"""
        if self._is_dragging:
            return
        cursor_pos = QCursor.pos()
        cursor_screen = QApplication.screenAt(cursor_pos)
        btn_screen = QApplication.screenAt(self.geometry().center())
        if cursor_screen is not None and cursor_screen != btn_screen:
            geo = cursor_screen.availableGeometry()
            x = geo.right() - self.width() - 20
            y = geo.center().y() - self.height() // 2
            self.move(x, y)

    # ── 拖拽 ───────────────────────────────────────────

    def _position_initial(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - self.width() - 20
            y = geo.center().y() - self.height() // 2
            self.move(x, y)

    @staticmethod
    def _screen_areas() -> list[ScreenArea]:
        return [ScreenArea(screen.name(), screen.availableGeometry()) for screen in QApplication.screens()]

    def ensure_visible(self) -> None:
        state = WindowState(self.x(), self.y(), screen=self._screen_name())
        fitted = fit_window_state(
            state,
            self._screen_areas(),
            fallback_size=self.size(),
            minimum_size=self.size(),
        )
        if fitted is not None:
            self.move(fitted[0].topLeft())

    def restore_placement(self, raw: str) -> bool:
        state = parse_window_state(raw, include_size=False)
        if state is None:
            return False
        fitted = fit_window_state(
            state,
            self._screen_areas(),
            fallback_size=self.size(),
            minimum_size=self.size(),
        )
        if fitted is None:
            return False
        self.move(fitted[0].topLeft())
        return True

    def _screen_name(self) -> str:
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        return screen.name() if screen is not None else ""

    def placement_state(self) -> str:
        return serialize_window_state(self.geometry(), self._screen_name(), include_size=False)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.placement_changed.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self._is_dragging = True
            new_pos = event.globalPos() - self._drag_pos
            screen = QApplication.screenAt(event.globalPos())
            if screen:
                geo = screen.availableGeometry()
                x = max(geo.left(), min(new_pos.x(), geo.right() - self.width()))
                y = max(geo.top(), min(new_pos.y(), geo.bottom() - self.height()))
                self.move(x, y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._is_dragging = False
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._create_context_menu().exec_(event.globalPos())

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(styles.menu_style())
        if self._pet_enabled and self._quick_actions:
            heading = menu.addAction("最近快捷动作")
            heading.setEnabled(False)
            busy = self._responding or self._listening
            for action_id, name in self._quick_actions:
                action = menu.addAction(f"⚡  {name}")
                action.setData(action_id)
                action.setEnabled(not busy)
                action.triggered.connect(
                    lambda _checked=False, selected=action_id: self.quick_action_requested.emit(
                        selected
                    )
                )
            menu.addSeparator()
        auto_hide_action = menu.addAction("自动收起对话框")
        auto_hide_action.setCheckable(True)
        auto_hide_action.setChecked(self._auto_hide)
        auto_hide_action.toggled.connect(self.auto_hide_toggled.emit)
        pet_action = menu.addAction("桌面宠物形态")
        pet_action.setCheckable(True)
        pet_action.setChecked(self._pet_enabled)
        pet_action.toggled.connect(self.pet_mode_toggled.emit)
        menu.addSeparator()
        settings_action = menu.addAction("设置…")
        settings_action.triggered.connect(self.settings_requested.emit)
        hide_action = menu.addAction("隐藏桌面宠物" if self._pet_enabled else "隐藏悬浮球")
        hide_action.triggered.connect(self.hide_requested.emit)
        about_action = menu.addAction("关于 AI 桌面助手")
        about_action.triggered.connect(self.about_requested.emit)
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.exit_requested.emit)
        return menu

    def set_auto_hide_state(self, enabled: bool) -> None:
        self._auto_hide = enabled
