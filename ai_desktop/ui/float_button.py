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
_PET_IDLE_FRAMES_PATH = resource_path("ai_desktop", "pet_frames", "idle.png")
_PET_HOVER_FRAMES_PATH = resource_path("ai_desktop", "pet_frames", "hover.png")
_PET_FRAME_COUNT = 5

_COMPACT_SIZE = 44
_PET_SIZES = {
    "small": QSize(92, 97),
    "medium": QSize(116, 122),
    "large": QSize(140, 147),
}
_DEFAULT_PET_SIZE = "medium"


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


def _load_pet_frames(path: str) -> list[QPixmap]:
    """Load a horizontal transparent sprite sheet and normalize its frame bounds."""
    if not os.path.exists(path):
        return []
    sheet = QPixmap(path)
    if sheet.isNull() or sheet.width() < _PET_FRAME_COUNT:
        return []

    frames: list[QPixmap] = []
    bounds = QRegion()
    frame_width = sheet.width() // _PET_FRAME_COUNT
    for index in range(_PET_FRAME_COUNT):
        frame = sheet.copy(index * frame_width, 0, frame_width, sheet.height())
        frame_bounds = QRegion(frame.mask()).boundingRect()
        if frame_bounds.isNull():
            return []
        frames.append(frame)
        bounds = QRegion(frame_bounds) if bounds.isEmpty() else bounds.united(frame_bounds)

    common = bounds.boundingRect()
    return [frame.copy(common.intersected(frame.rect())) for frame in frames]


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
    screenshot_requested = pyqtSignal()
    placement_changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        pet_enabled: bool = True,
        reduce_motion: bool = False,
        pet_size: str = _DEFAULT_PET_SIZE,
    ):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._auto_hide = False
        self._is_dragging: bool = False
        self._pet_enabled = bool(pet_enabled and os.path.exists(_PET_PATH))
        self._reduce_motion = bool(reduce_motion)
        self._pet_size = self._normalize_pet_size(pet_size)
        self._listening = False
        self._responding: bool = False
        self._result_state: str | None = None
        self._hovered = False
        self._quick_actions: list[tuple[str, str]] = []
        self._animation_phase = 0
        self._hover_phase = 0
        self._pet_source = QPixmap(_PET_PATH) if os.path.exists(_PET_PATH) else QPixmap()
        self._pet_content = self._cropped_pet(self._pet_source)
        self._pet_idle_frames = _load_pet_frames(_PET_IDLE_FRAMES_PATH)
        self._pet_hover_frames = _load_pet_frames(_PET_HOVER_FRAMES_PATH)
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

    @staticmethod
    def _normalize_pet_size(value: str) -> str:
        value = str(value).strip().lower()
        return value if value in _PET_SIZES else _DEFAULT_PET_SIZE

    def _apply_mode(self) -> None:
        if self._pet_enabled and not self._pet_content.isNull():
            self.setFixedSize(_PET_SIZES[self._pet_size])
            self.setIcon(QIcon())
            self.setText("")
            self.setStyleSheet("QPushButton { background: transparent; border: none; }")
            self.setToolTip(self._state_tooltip())
            self._sync_animation_timer()
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
        self._sync_animation_timer()

    def _sync_animation_timer(self) -> None:
        should_animate = bool(
            self.isVisible()
            and not self._reduce_motion
            and (self._pet_enabled or self._responding)
        )
        if should_animate:
            self._animation_timer.start()
        else:
            self._animation_timer.stop()

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

    @property
    def reduce_motion(self) -> bool:
        return self._reduce_motion

    def set_reduce_motion(self, enabled: bool) -> None:
        self._reduce_motion = bool(enabled)
        if self._reduce_motion:
            self._animation_phase = 0
            self._hover_phase = 0
            self.setWindowOpacity(1.0)
        self._sync_animation_timer()
        self.update()

    @property
    def pet_size(self) -> str:
        return self._pet_size

    def set_pet_size(self, size: str) -> None:
        size = self._normalize_pet_size(size)
        if size == self._pet_size:
            return
        anchor = self.geometry().bottomRight()
        self._pet_size = size
        if self._pet_enabled:
            self.setFixedSize(_PET_SIZES[size])
            self.move(anchor.x() - self.width() + 1, anchor.y() - self.height() + 1)
            self.ensure_visible()
        self.update()

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

        scale = min(self.width() / 116, self.height() / 122)
        def px(value: float) -> int:
            return max(1, round(value * scale))

        state = self._effective_state()
        offset_x, offset_y, rotation, motion_scale = self._motion_for_state(state)
        if state == "idle" and self._hovered and not self._reduce_motion:
            hover_x, hover_y, hover_rotation, hover_scale = self._hover_motion()
            offset_x += hover_x
            offset_y += hover_y
            rotation += hover_rotation
            motion_scale *= hover_scale
        target = QRectF(self.rect()).adjusted(
            px(4),
            px(5),
            -px(4),
            -px(7),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(14, 24, 55, 38))
        painter.drawEllipse(
            px(24), self.height() - px(10), self.width() - px(48), px(6)
        )
        self._draw_hover_halo(painter, state, scale)
        self._draw_working_glow(painter, state)

        center = target.center()
        painter.save()
        painter.translate(center.x() + offset_x * scale, center.y() + offset_y * scale)
        painter.rotate(rotation)
        painter.scale(motion_scale, motion_scale)
        painter.translate(-center.x(), -center.y())
        pet_content = self._pet_for_state(state)
        painter.drawPixmap(target, pet_content, QRectF(pet_content.rect()))
        painter.restore()

        if state == "success":
            self._draw_success_sparkles(painter, px)

        if state != "idle":
            bubble_width = px(38 if state == "working" else 30)
            bubble = QRectF(
                self.width() - bubble_width - px(3),
                px(3),
                bubble_width,
                px(24),
            )
            painter.setPen(QPen(QColor(92, 207, 255, 210), 1.5 * scale))
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.drawRoundedRect(bubble, px(12), px(12))
            if state == "listening":
                painter.setPen(QPen(QColor(20, 142, 235, 230), 2.2 * scale))
                center_x = bubble.center().x()
                painter.drawLine(
                    round(center_x), round(bubble.top() + px(6)),
                    round(center_x), round(bubble.bottom() - px(7)),
                )
                painter.drawLine(
                    round(center_x), round(bubble.bottom() - px(7)),
                    round(center_x - px(4)), round(bubble.bottom() - px(11)),
                )
                painter.drawLine(
                    round(center_x), round(bubble.bottom() - px(7)),
                    round(center_x + px(4)), round(bubble.bottom() - px(11)),
                )
            elif state == "working":
                active_dot = (
                    None if self._reduce_motion else (self._animation_phase // 2) % 3
                )
                for index in range(3):
                    alpha = 190 if active_dot is None else (235 if index == active_dot else 90)
                    painter.setBrush(QColor(20, 142, 235, alpha))
                    x = bubble.left() + bubble.width() / 2 + (index - 1) * px(7)
                    radius = px(2)
                    painter.drawEllipse(
                        QRectF(
                            x - radius,
                            bubble.center().y() - radius,
                            radius * 2,
                            radius * 2,
                        )
                    )
            elif state == "success":
                painter.setPen(QPen(QColor(27, 166, 93, 235), 2.3 * scale))
                center = bubble.center()
                painter.drawLine(
                    round(center.x() - px(5)), round(center.y()),
                    round(center.x() - px(1)), round(center.y() + px(4)),
                )
                painter.drawLine(
                    round(center.x() - px(1)), round(center.y() + px(4)),
                    round(center.x() + px(6)), round(center.y() - px(5)),
                )
            else:
                painter.setPen(QPen(QColor(218, 75, 87, 235), 2.3 * scale))
                center = bubble.center()
                painter.drawLine(
                    round(center.x()), round(center.y() - px(6)),
                    round(center.x()), round(center.y() + px(2)),
                )
                painter.setBrush(QColor(218, 75, 87, 235))
                painter.setPen(Qt.NoPen)
                radius = 1.5 * scale
                painter.drawEllipse(
                    QRectF(
                        center.x() - radius,
                        center.y() + px(5),
                        radius * 2,
                        radius * 2,
                    )
                )
        painter.end()

    def _motion_for_state(self, state: str) -> tuple[float, float, float, float]:
        """Return x/y movement, rotation and scale for a semantic pet state."""
        if self._reduce_motion:
            return (0.0, 0.0, 0.0, 1.0)
        wave = math.sin(self._animation_phase * math.pi / 6)
        if state == "listening":
            return (wave * 0.7, -abs(wave) * 0.8, -wave * 1.6, 1.005)
        if state == "working":
            return (0.0, wave * 1.8, 0.0, 1.0 + (wave + 1.0) * 0.004)
        if state == "success":
            bounce = -abs(math.sin(self._animation_phase * math.pi / 8)) * 4.0
            return (0.0, bounce, 0.0, 1.0 + max(0.0, -bounce) * 0.006)
        if state == "error":
            shake = (0.0, -2.2, 2.2, -1.5, 1.5, -0.7, 0.7, 0.0)
            return (shake[min(self._animation_phase, len(shake) - 1)], 0.0, 0.0, 1.0)
        idle_phase = self._animation_phase % 96
        # Five low-key idle clips.  The longer breathing intervals leave ample
        # quiet time between the little observations and grooming gestures.
        if 24 <= idle_phase < 32:  # 左右观察
            progress = (idle_phase - 24) / 7
            look = math.sin(progress * math.pi)
            return (
                math.sin(progress * math.pi * 2) * 0.9,
                wave * 1.15 - look * 0.6,
                math.sin(progress * math.pi * 2) * 2.0,
                1.0,
            )
        if 40 <= idle_phase < 48:  # 舒展
            stretch = math.sin((idle_phase - 40) * math.pi / 7)
            return (0.0, wave * 1.15 - stretch * 2.2, 0.0, 1.0 + stretch * 0.018)
        if 58 <= idle_phase < 64:  # 轻点头
            nod = math.sin((idle_phase - 58) * math.pi / 5)
            return (0.0, wave * 1.15 + nod * 1.5, 0.0, 1.0 - nod * 0.009)
        if 74 <= idle_phase < 84:  # 整理羽毛
            groom = math.sin((idle_phase - 74) * math.pi / 9)
            return (groom * 1.1, wave * 1.15 - groom * 0.7, groom * 1.5, 1.0)
        return (0.0, wave * 1.15, 0.0, 1.0)  # 呼吸

    def _pet_for_state(self, state: str) -> QPixmap:
        """Select a generated frame while keeping the original asset as fallback."""
        if state == "idle":
            if self._hovered and self._pet_hover_frames:
                return self._pet_hover_frames[min(self._hover_phase // 10, _PET_FRAME_COUNT - 1)]
            if self._pet_idle_frames:
                return self._pet_idle_frames[min(self._animation_phase // 20, _PET_FRAME_COUNT - 1)]
        return self._pet_content

    def _hover_motion(self) -> tuple[float, float, float, float]:
        """Return one of five friendly hover clips for the idle pet."""
        if self._reduce_motion or not self._hovered:
            return (0.0, 0.0, 0.0, 1.0)
        phase = self._hover_phase % 50
        if phase < 10:  # 抬头致意
            greeting = math.sin(phase * math.pi / 9)
            return (0.0, -greeting * 2.8, -greeting * 1.2, 1.0 + greeting * 0.028)
        if phase < 20:  # 专注侧倾
            focus = math.sin((phase - 10) * math.pi / 9)
            return (focus * 0.5, -1.5 - focus * 0.5, focus * 2.3, 1.028)
        if phase < 30:  # 开心跳跃
            bounce = math.sin((phase - 20) * math.pi / 9)
            return (0.0, -bounce * 3.2, 0.0, 1.0 + bounce * 0.036)
        if phase < 40:  # 轻挥翅膀
            wave = math.sin((phase - 30) * math.pi * 2 / 9)
            return (wave * 0.8, -1.2, -wave * 2.2, 1.024)
        # 安静回望，给下一轮致意留出过渡。
        settle = math.sin((phase - 40) * math.pi / 9)
        return (0.0, -settle * 1.2, -settle * 0.8, 1.0 + settle * 0.014)

    def _draw_hover_halo(self, painter: QPainter, state: str, scale: float) -> None:
        """Draw a quiet hover halo without changing task states."""
        if state != "idle" or not self._hovered or self._reduce_motion:
            return
        phase = self._hover_phase % 50
        if not 10 <= phase < 30:
            return
        pulse = (math.sin((phase - 10) * math.pi / 10) + 1.0) / 2.0
        bounds = QRectF(
            self.width() * 0.17,
            self.height() * 0.14,
            self.width() * 0.66,
            self.height() * 0.68,
        )
        painter.setPen(QPen(QColor(92, 207, 255, round(52 + pulse * 48)), 1.2 * scale))
        painter.setBrush(QColor(92, 207, 255, round(5 + pulse * 10)))
        painter.drawEllipse(bounds)

    def _draw_working_glow(self, painter: QPainter, state: str) -> None:
        if state != "working":
            return
        pulse = (math.sin(self._animation_phase * math.pi / 6) + 1.0) / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(67, 207, 255, round(18 + pulse * 28)))
        painter.drawEllipse(
            QRectF(
                self.width() * 0.43,
                self.height() * 0.50,
                self.width() * 0.51,
                self.height() * 0.34,
            )
        )

    def _draw_success_sparkles(self, painter: QPainter, px) -> None:
        pulse = (math.sin(self._animation_phase * math.pi / 4) + 1.0) / 2.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(73, 220, 145, round(130 + pulse * 110)))
        for x, y, radius in ((18, 25, 2.2), (26, 13, 1.4), (96, 45, 1.8)):
            painter.drawEllipse(QRectF(px(x), px(y), px(radius * 2), px(radius * 2)))

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
        if self._reduce_motion:
            self._animation_timer.stop()
            return
        # 96 keeps the original 12-frame task waves intact and leaves enough
        # room for five relaxed idle clips before the sequence repeats.
        self._animation_phase = (self._animation_phase + 1) % 96
        if self._hovered and self._effective_state() == "idle":
            self._hover_phase = (self._hover_phase + 1) % 50
        if not self._pet_enabled:
            if self._responding:
                self.setWindowOpacity(
                    0.55 if self._animation_phase % 12 < 6 else 1.0
                )
            return
        self.update()

    def enterEvent(self, event) -> None:
        if not self._hovered:
            self._hover_phase = 0
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._hover_phase = 0
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        self._sync_animation_timer()
        if hasattr(self, "_track_timer"):
            self._track_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._animation_timer.stop()
        self._result_timer.stop()
        if hasattr(self, "_track_timer"):
            self._track_timer.stop()
        self._result_state = None
        super().hideEvent(event)

    # ── 响应状态脉冲 ───────────────────────────────────

    def set_responding(self, responding: bool) -> None:
        """设置 AI 生成状态。"""
        if responding and not self._responding:
            self._animation_phase = 0
        self._responding = responding
        if responding:
            self._result_timer.stop()
            self._result_state = None
        self._sync_animation_timer()
        self.setWindowOpacity(1.0)
        self.setToolTip(self._state_tooltip())
        self.update()

    def show_result(self, succeeded: bool) -> None:
        """短暂显示本次请求结果，然后恢复空闲状态。"""
        self._animation_phase = 0
        self._result_state = "success" if succeeded else "error"
        if self.isVisible():
            self._result_timer.start(1600)
        else:
            self._result_state = None
        self.setToolTip(self._state_tooltip())
        self.update()

    def _clear_result_state(self) -> None:
        self._result_state = None
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_listening(self, listening: bool) -> None:
        """设置选区或截图捕获状态；生成状态始终优先。"""
        if listening and not self._listening:
            self._animation_phase = 0
        self._listening = listening
        self.setToolTip(self._state_tooltip())
        self.update()

    # ── 屏幕跟随 ───────────────────────────────────────

    def _start_screen_tracking(self) -> None:
        """定时检测鼠标所在屏幕，自动跟随"""
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(500)
        self._track_timer.timeout.connect(self._follow_cursor_screen)
        if self.isVisible():
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
        busy = self._responding or self._listening
        screenshot_action = menu.addAction("截图到对话…")
        screenshot_action.setData("screenshot")
        screenshot_action.setEnabled(not busy)
        screenshot_action.triggered.connect(self.screenshot_requested.emit)
        menu.addSeparator()
        if self._pet_enabled and self._quick_actions:
            heading = menu.addAction("最近快捷动作")
            heading.setEnabled(False)
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
