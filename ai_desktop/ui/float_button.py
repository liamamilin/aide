"""桌面宠物入口：可拖拽、跨 Spaces，并显示捕获与生成状态。"""
import ctypes
import ctypes.util
import math
import os
import random
import sys

from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PyQt5.QtWidgets import QApplication, QMenu, QPushButton

from ai_desktop.ui import styles
from ai_desktop.ui.pet_layers import load_eye_expressions
from ai_desktop.utils.paths import resource_path
from ai_desktop.utils.window_state import (
    ScreenArea,
    WindowState,
    fit_window_state,
    parse_window_state,
    serialize_window_state,
)

_ICON_PATH = next(
    (resource_path("ai_desktop", f) for f in ("图标-v2.png", "图标.icns", "图标.png")
     if os.path.exists(resource_path("ai_desktop", f))),
    resource_path("ai_desktop", "图标.png"),
)
_PET_PATH = resource_path("ai_desktop", "桌面宠物-v2.png")
_PET_LAYERS_PATH = resource_path("ai_desktop", "pet_layers", "master.json")
_PET_HOVER_PHASES = 8
_PET_HOVER_RETURN_PHASES = 4

_COMPACT_SIZE = 44
_PET_SIZES = {
    "small": QSize(92, 97),
    "medium": QSize(104, 110),
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


def _visible_pet_bounds(source: QPixmap) -> QRect:
    """Ignore nearly transparent generation noise when finding the character."""
    if source.isNull():
        return QRect()
    image = source.toImage().convertToFormat(QImage.Format_RGBA8888)
    pointer = image.bits()
    pointer.setsize(image.byteCount())
    pixels = bytes(pointer)
    alpha_map = bytes(1 if alpha >= 16 else 0 for alpha in range(256))
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        start = y * image.bytesPerLine() + 3
        alphas = pixels[start:start + image.width() * 4:4].translate(alpha_map)
        first = alphas.find(b"\x01")
        if first < 0:
            continue
        left = min(left, first)
        right = max(right, alphas.rfind(b"\x01"))
        top = min(top, y)
        bottom = y
    if right < left:
        return QRect()
    return QRect(left, top, right - left + 1, bottom - top + 1)


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
    read_selection_requested = pyqtSignal()
    stop_speech_requested = pyqtSignal()
    screenshot_requested = pyqtSignal()
    placement_changed = pyqtSignal()
    screen_follow_changed = pyqtSignal(bool)

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
        self._press_global: QPoint | None = None
        self._auto_hide = False
        self._is_dragging: bool = False
        self._follow_cursor_screen_enabled = True
        self._pet_enabled = bool(pet_enabled and os.path.exists(_PET_PATH))
        self._reduce_motion = bool(reduce_motion)
        self._pet_size = self._normalize_pet_size(pet_size)
        self._listening = False
        self._speaking = False
        self._responding: bool = False
        self._result_state: str | None = None
        self._hovered = False
        self._quick_actions: list[tuple[str, str]] = []
        self._animation_phase = 0
        self._working_intro_phase = 0
        self._hover_phase = 0
        self._hover_return_phase = _PET_HOVER_RETURN_PHASES
        self._hover_release_y = 0.0
        self._hover_release_scale = 1.0
        self._blink_rng = random.Random()
        self._idle_blinking = False
        self._pet_source = QPixmap(_PET_PATH) if os.path.exists(_PET_PATH) else QPixmap()
        self._pet_crop = self._pet_crop_rect(self._pet_source)
        self._pet_content = self._crop_pet(self._pet_source, self._pet_crop)
        expressions = load_eye_expressions(self._pet_source, self._pet_crop, _PET_LAYERS_PATH)
        self._pet_blink_content = expressions.get("blink", self._pet_content)
        self._pet_attentive_content = expressions.get("attentive", self._pet_content)
        self._pet_focused_content = expressions.get("focused", self._pet_content)
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(180)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._idle_blink_timer = QTimer(self)
        self._idle_blink_timer.setSingleShot(True)
        self._idle_blink_timer.timeout.connect(self._begin_idle_blink)
        self._blink_open_timer = QTimer(self)
        self._blink_open_timer.setSingleShot(True)
        self._blink_open_timer.timeout.connect(self._end_idle_blink)
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
    def _pet_crop_rect(source: QPixmap) -> QRect:
        if source.isNull():
            return QRect()
        bounds = _visible_pet_bounds(source)
        if bounds.isNull():
            return source.rect()
        margin = max(8, int(min(bounds.width(), bounds.height()) * 0.035))
        return bounds.adjusted(-margin, -margin, margin, margin).intersected(source.rect())

    @staticmethod
    def _crop_pet(source: QPixmap, crop: QRect) -> QPixmap:
        return source.copy(crop) if not source.isNull() and not crop.isNull() else source

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
            self._idle_hit_mask = self._build_idle_hit_mask()
            self._sync_hit_mask()
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
        self.setMask(QRegion(self.rect(), QRegion.Ellipse))
        self._sync_animation_timer()

    def _pet_draw_rect(self, pet_content: QPixmap) -> QRectF:
        """Use one placement calculation for rendering and mouse hit geometry."""
        scale = min(self.width() / 116, self.height() / 122)
        inset_x = max(1, round(4 * scale))
        inset_top = max(1, round(5 * scale))
        inset_bottom = max(1, round(7 * scale))
        target = QRectF(self.rect()).adjusted(
            inset_x, inset_top, -inset_x, -inset_bottom
        )
        fitted_height = min(
            target.height(),
            target.width() * pet_content.height() / pet_content.width(),
        )
        fitted_width = fitted_height * pet_content.width() / pet_content.height()
        return QRectF(
            target.center().x() - fitted_width / 2,
            target.bottom() - fitted_height,
            fitted_width,
            fitted_height,
        )

    def _build_idle_hit_mask(self) -> QRegion:
        """Keep transparent window corners click-through while preserving the owl."""
        image = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(
            self._pet_draw_rect(self._pet_content),
            self._pet_content,
            QRectF(self._pet_content.rect()),
        )
        painter.end()

        # Leave room for the brief hover lift and anti-aliased feather edges.
        margin = max(5, round(min(self.width(), self.height()) * 0.06))
        region = QRegion()
        for y in range(image.height()):
            x = 0
            while x < image.width():
                if image.pixelColor(x, y).alpha() < 24:
                    x += 1
                    continue
                start = x
                while x < image.width() and image.pixelColor(x, y).alpha() >= 24:
                    x += 1
                region |= QRegion(
                    QRect(
                        max(0, start - margin),
                        max(0, y - margin),
                        min(image.width(), x + margin) - max(0, start - margin),
                        min(image.height(), y + margin + 1) - max(0, y - margin),
                    )
                )
        scale = min(self.width() / 116, self.height() / 122)
        def px(value: float) -> int:
            return max(1, round(value * scale))
        shadow = QRect(
            px(24), self.height() - px(10), self.width() - px(48), px(6)
        )
        return region | QRegion(shadow.adjusted(-3, -2, 3, 2), QRegion.Ellipse)

    def _sync_hit_mask(self) -> None:
        if not self._pet_enabled or self._pet_content.isNull():
            return
        # Badges and task glow may extend beyond the silhouette; idle is the
        # long-lived state where transparent corners should pass clicks through.
        mask = (
            self._idle_hit_mask
            if self._effective_state() == "idle"
            else QRegion(self.rect())
        )
        if self.mask() != mask:
            self.setMask(mask)

    def _sync_animation_timer(self) -> None:
        state = self._effective_state()
        idle_pet = bool(
            self.isVisible()
            and self._pet_enabled
            and not self._reduce_motion
            and state == "idle"
        )
        if idle_pet and not self._hovered and self._hover_return_phase >= _PET_HOVER_RETURN_PHASES:
            if not self._idle_blinking and not self._idle_blink_timer.isActive():
                self._schedule_idle_blink()
        else:
            self._idle_blink_timer.stop()
            self._blink_open_timer.stop()
            if self._idle_blinking:
                self._idle_blinking = False
                self.update()
        pet_motion = self._pet_enabled and (
            state != "idle"
            or (self._hovered and self._hover_phase < _PET_HOVER_PHASES)
            or self._hover_return_phase < _PET_HOVER_RETURN_PHASES
        )
        should_animate = self.isVisible() and not self._reduce_motion and (
            self._responding or pet_motion
        )
        if should_animate:
            self._animation_timer.start()
        else:
            self._animation_timer.stop()

    def _schedule_idle_blink(self) -> None:
        """Vary the pause so the owl does not blink on a visible metronome."""
        self._idle_blink_timer.start(self._blink_rng.randint(3800, 7600))

    def _begin_idle_blink(self) -> None:
        if (
            not self.isVisible()
            or not self._pet_enabled
            or self._reduce_motion
            or self._hovered
            or self._effective_state() != "idle"
        ):
            return
        self._idle_blinking = True
        self._blink_open_timer.start(115)
        self.update()

    def _end_idle_blink(self) -> None:
        self._idle_blinking = False
        self.update()
        self._sync_animation_timer()

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
            self._hover_return_phase = _PET_HOVER_RETURN_PHASES
            self._idle_blinking = False
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
            self._idle_hit_mask = self._build_idle_hit_mask()
            self._sync_hit_mask()
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
        if state == "idle" and not self._reduce_motion and (
            self._hovered or self._hover_return_phase < _PET_HOVER_RETURN_PHASES
        ):
            hover_x, hover_y, hover_rotation, hover_scale = self._hover_motion()
            offset_x += hover_x
            offset_y += hover_y
            rotation += hover_rotation
            motion_scale *= hover_scale
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(14, 24, 55, 38))
        painter.drawEllipse(
            px(24), self.height() - px(10), self.width() - px(48), px(6)
        )
        if not self._reduce_motion:
            self._draw_working_glow(painter, state)

        pet_content = self._pet_for_state(state)
        draw_rect = self._pet_draw_rect(pet_content)
        center = draw_rect.center()
        painter.save()
        painter.translate(center.x() + offset_x * scale, center.y() + offset_y * scale)
        painter.rotate(rotation)
        painter.scale(motion_scale, motion_scale)
        painter.translate(-center.x(), -center.y())
        # Draw exactly one pose; alpha cross-fades duplicate feather edges.
        painter.drawPixmap(draw_rect, pet_content, QRectF(pet_content.rect()))
        painter.restore()

        if state == "success" and not self._reduce_motion:
            self._draw_success_sparkles(painter, px)

        if state != "idle":
            bubble_width = px(38 if state in {"working", "speaking"} else 30)
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
            elif state == "speaking":
                active_bar = (
                    None if self._reduce_motion else (self._animation_phase // 2) % 3
                )
                center_y = bubble.center().y()
                for index, base_height in enumerate((7, 12, 8)):
                    height = px(
                        base_height
                        if active_bar is None
                        else base_height + (4 if index == active_bar else 0)
                    )
                    x = bubble.left() + bubble.width() / 2 + (index - 1) * px(7)
                    painter.setPen(
                        QPen(QColor(20, 142, 235, 230), max(1.5, 2.0 * scale))
                    )
                    painter.drawLine(
                        round(x),
                        round(center_y - height / 2),
                        round(x),
                        round(center_y + height / 2),
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
            if self._working_intro_phase >= 6:
                return (0.0, 0.0, 0.0, 1.0)
            progress = min(self._working_intro_phase, 6) / 6
            settle = math.sin(progress * math.pi) ** 2
            return (0.0, -settle * 0.9, 0.0, 1.0 + settle * 0.005)
        if state == "speaking":
            return (0.0, wave * 0.7, 0.0, 1.0 + abs(wave) * 0.002)
        if state == "success":
            bounce = -abs(math.sin(self._animation_phase * math.pi / 8)) * 4.0
            return (0.0, bounce, 0.0, 1.0 + max(0.0, -bounce) * 0.006)
        if state == "error":
            shake = (0.0, -2.2, 2.2, -1.5, 1.5, -0.7, 0.7, 0.0)
            return (shake[min(self._animation_phase, len(shake) - 1)], 0.0, 0.0, 1.0)
        return (0.0, 0.0, 0.0, 1.0)

    def _pet_for_state(self, state: str) -> QPixmap:
        """Swap only the eye layer; the body, laptop and feet stay pixel-stable."""
        if self._reduce_motion:
            return self._pet_content
        if state == "working" and self._working_intro_phase >= 1:
            return self._pet_focused_content
        if state == "idle":
            if self._hovered:
                if self._hover_phase == 4:
                    return self._pet_blink_content
                if self._hover_phase >= 1:
                    return self._pet_attentive_content
            elif self._hover_return_phase < _PET_HOVER_RETURN_PHASES:
                return self._pet_attentive_content
            elif self._idle_blinking:
                return self._pet_blink_content
        return self._pet_content

    def _hover_motion(self) -> tuple[float, float, float, float]:
        """Return one continuous, eased greeting motion for the idle pet."""
        if self._reduce_motion:
            return (0.0, 0.0, 0.0, 1.0)
        if not self._hovered:
            if self._hover_return_phase >= _PET_HOVER_RETURN_PHASES:
                return (0.0, 0.0, 0.0, 1.0)
            progress = self._hover_return_phase / _PET_HOVER_RETURN_PHASES
            remaining = 1.0 - progress * progress * (3.0 - 2.0 * progress)
            return (
                0.0,
                self._hover_release_y * remaining,
                0.0,
                1.0 + (self._hover_release_scale - 1.0) * remaining,
            )
        phase = min(self._hover_phase, _PET_HOVER_PHASES)
        if phase >= _PET_HOVER_PHASES:
            return (0.0, 0.0, 0.0, 1.0)
        progress = phase / _PET_HOVER_PHASES
        lift = math.sin(progress * math.pi) ** 2
        return (0.0, -lift * 1.8, 0.0, 1.0 + lift * 0.012)

    def _draw_working_glow(self, painter: QPainter, state: str) -> None:
        if state not in {"working", "speaking"}:
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
        if self._speaking:
            return "speaking"
        if self._listening:
            return "listening"
        if self._result_state:
            return self._result_state
        return "idle"

    def _state_tooltip(self) -> str:
        if self._effective_state() == "idle":
            placement = (
                "拖动可固定位置" if self._follow_cursor_screen_enabled
                else "已固定屏幕，右键可重新跟随"
            )
            return f"AI 桌面宠物 · 点击对话，{placement}"
        return {
            "listening": "正在读取选中内容…",
            "speaking": "正在朗读 · 右键可停止",
            "working": "AI 正在回复…",
            "success": "回复已完成",
            "error": "这次没有完成，可点击查看",
        }[self._effective_state()]

    def _advance_animation(self) -> None:
        if self._reduce_motion:
            self._animation_timer.stop()
            return
        # Task states keep their own loop; idle blinking has a separate timer.
        self._animation_phase = (self._animation_phase + 1) % 96
        if self._responding:
            self._working_intro_phase = min(self._working_intro_phase + 1, 6)
        if self._hovered and self._effective_state() == "idle":
            self._hover_phase = min(self._hover_phase + 1, _PET_HOVER_PHASES)
        elif self._hover_return_phase < _PET_HOVER_RETURN_PHASES:
            self._hover_return_phase += 1
        if not self._pet_enabled:
            if self._responding:
                self.setWindowOpacity(
                    0.55 if self._animation_phase % 12 < 6 else 1.0
                )
            return
        self.update()
        if self._effective_state() == "idle" and (
            self._hovered and self._hover_phase >= _PET_HOVER_PHASES
            or not self._hovered and self._hover_return_phase >= _PET_HOVER_RETURN_PHASES
        ):
            self._sync_animation_timer()

    def enterEvent(self, event) -> None:
        if not self._hovered:
            self._hover_phase = (
                0 if self._effective_state() == "idle" else _PET_HOVER_PHASES
            )
        self._hover_return_phase = _PET_HOVER_RETURN_PHASES
        self._hovered = True
        self._sync_animation_timer()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        _, self._hover_release_y, _, self._hover_release_scale = self._hover_motion()
        self._hover_return_phase = (
            0 if abs(self._hover_release_y) >= 0.1 else _PET_HOVER_RETURN_PHASES
        )
        self._hovered = False
        self._hover_phase = 0
        self._sync_animation_timer()
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        self._sync_hit_mask()
        self._sync_animation_timer()
        if hasattr(self, "_track_timer") and self._follow_cursor_screen_enabled:
            self._track_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._animation_timer.stop()
        self._idle_blink_timer.stop()
        self._blink_open_timer.stop()
        self._idle_blinking = False
        self._hover_return_phase = _PET_HOVER_RETURN_PHASES
        self._result_timer.stop()
        if hasattr(self, "_track_timer"):
            self._track_timer.stop()
        self._result_state = None
        self._sync_hit_mask()
        super().hideEvent(event)

    # ── 响应状态脉冲 ───────────────────────────────────

    def set_responding(self, responding: bool) -> None:
        """设置 AI 生成状态。"""
        if responding and not self._responding:
            self._animation_phase = 0
            self._working_intro_phase = 0
            self._settle_hover_for_task()
        self._responding = responding
        if responding:
            self._result_timer.stop()
            self._result_state = None
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setWindowOpacity(1.0)
        self.setToolTip(self._state_tooltip())
        self.update()

    def show_result(self, succeeded: bool) -> None:
        """短暂显示本次请求结果，然后恢复空闲状态。"""
        self._animation_phase = 0
        self._settle_hover_for_task()
        self._result_state = "success" if succeeded else "error"
        if self.isVisible():
            self._result_timer.start(1600)
        else:
            self._result_state = None
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    def _clear_result_state(self) -> None:
        self._result_state = None
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_listening(self, listening: bool) -> None:
        """设置选区或截图捕获状态；生成状态始终优先。"""
        if listening and not self._listening:
            self._animation_phase = 0
            self._settle_hover_for_task()
        self._listening = listening
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_speaking(self, speaking: bool) -> None:
        """设置语音生成或播放状态。"""
        if speaking and not self._speaking:
            self._animation_phase = 0
            self._settle_hover_for_task()
        self._speaking = speaking
        if speaking:
            self._result_timer.stop()
            self._result_state = None
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    # ── 屏幕跟随 ───────────────────────────────────────

    @property
    def follow_cursor_screen(self) -> bool:
        return self._follow_cursor_screen_enabled

    def set_follow_cursor_screen(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._follow_cursor_screen_enabled:
            return
        self._follow_cursor_screen_enabled = enabled
        self.screen_follow_changed.emit(enabled)
        if self._pet_enabled and self._effective_state() == "idle":
            self.setToolTip(self._state_tooltip())
        if hasattr(self, "_track_timer"):
            if enabled and self.isVisible():
                self._track_timer.start()
            else:
                self._track_timer.stop()
        if enabled:
            self._follow_cursor_screen()

    def _settle_hover_for_task(self) -> None:
        """A task supersedes any half-finished idle greeting."""
        self._hover_phase = _PET_HOVER_PHASES if self._hovered else 0
        self._hover_return_phase = _PET_HOVER_RETURN_PHASES
        self._hover_release_y = 0.0
        self._hover_release_scale = 1.0

    def _start_screen_tracking(self) -> None:
        """定时检测鼠标所在屏幕，自动跟随"""
        self._track_timer = QTimer(self)
        self._track_timer.setInterval(500)
        self._track_timer.timeout.connect(self._follow_cursor_screen)
        if self.isVisible() and self._follow_cursor_screen_enabled:
            self._track_timer.start()

    def _follow_cursor_screen(self) -> None:
        """如果鼠标所在的屏幕与按钮不同，移动按钮到鼠标所在屏幕"""
        if self._is_dragging or not self._follow_cursor_screen_enabled:
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
            self._press_global = event.globalPos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            if not self._is_dragging and self._press_global is not None:
                distance = (event.globalPos() - self._press_global).manhattanLength()
                if distance >= QApplication.startDragDistance():
                    self._is_dragging = True
                    self.setCursor(Qt.ClosedHandCursor)
                    self._settle_hover_for_task()
                    self._sync_animation_timer()
                    self.set_follow_cursor_screen(False)
            if not self._is_dragging:
                super().mouseMoveEvent(event)
                return
            new_pos = event.globalPos() - self._drag_pos
            screen = QApplication.screenAt(event.globalPos())
            if screen:
                geo = screen.availableGeometry()
                x = max(geo.left(), min(new_pos.x(), geo.right() - self.width()))
                y = max(geo.top(), min(new_pos.y(), geo.bottom() - self.height()))
                self.move(x, y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        dragged = self._is_dragging and event.button() == Qt.LeftButton
        self._is_dragging = False
        self._drag_pos = None
        self._press_global = None
        if dragged:
            self.setDown(False)
            self.setCursor(Qt.PointingHandCursor)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._create_context_menu().exec_(event.globalPos())

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(styles.menu_style())
        busy = self._responding or self._listening or self._speaking
        if self._speaking:
            read_action = menu.addAction("■ 停止朗读")
            read_action.setToolTip("停止当前语音")
            read_action.triggered.connect(self.stop_speech_requested.emit)
        else:
            read_action = menu.addAction("🔊 朗读选区")
            read_action.setEnabled(not busy)
            read_action.setToolTip("朗读其他应用中当前选中的英文")
            read_action.triggered.connect(self.read_selection_requested.emit)
        menu.addSeparator()
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
        follow_action = menu.addAction("跟随鼠标所在屏幕")
        follow_action.setCheckable(True)
        follow_action.setChecked(self._follow_cursor_screen_enabled)
        follow_action.setToolTip("拖动宠物后自动关闭；可在这里重新开启")
        follow_action.toggled.connect(self.set_follow_cursor_screen)
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
