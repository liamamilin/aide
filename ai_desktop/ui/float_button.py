"""桌面宠物入口：可拖拽、跨 Spaces，并显示捕获与生成状态。"""
import ctypes
import ctypes.util
import logging
import math
import os
import sys
import time
from pathlib import Path

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
from PyQt5.QtWidgets import QApplication, QPushButton

from ai_desktop import config
from ai_desktop.ui.fluent import Menu as QMenu
from ai_desktop.ui.motion_preference import MotionPreference
from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import ManifestError, load_manifest
from ai_desktop.ui.pet_profiles import apply_petdex_profile
from ai_desktop.ui.spritesheet import Spritesheet
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
_logger = logging.getLogger(__name__)


def _get_pet_manifest_path(pet_source: str = "built-in", pet_name: str = "owl-v2") -> str:
    """Get the manifest path for a pet from the specified source."""
    if not pet_name or Path(pet_name).name != pet_name or pet_name in {".", ".."}:
        pet_name = "owl-v2"
        pet_source = "built-in"
    if pet_source == "petdex":
        petdex_dir = Path.home() / ".petdex" / "pets" / pet_name
        manifest_path = petdex_dir / "pet.json"
        if manifest_path.exists():
            return str(manifest_path)
    return resource_path("ai_desktop", "pets", pet_name, "pet.json")

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
        self._pet_enabled_requested = bool(pet_enabled)
        self._motion_preference = MotionPreference(self)
        self._reduce_motion_requested = bool(reduce_motion)
        self._reduce_motion = bool(reduce_motion or self._motion_preference.reduced)
        self._motion_preference.changed.connect(self._on_system_motion_changed)
        self._pet_size = self._normalize_pet_size(pet_size)
        self._listening = False
        self._speaking = False
        self._responding: bool = False
        self._result_state: str | None = None
        self._hovered = False
        self._menu_open = False
        self._last_hover_reaction_time = float("-inf")
        self._quick_actions: list[tuple[str, str]] = []
        self._animation_phase = 0
        self._spritesheet: Spritesheet | None = None
        self._animator: PetAnimator | None = None
        self._spritesheet_frame: QPixmap = QPixmap()
        self._spritesheet_available = False
        self._last_animation_time = time.monotonic()
        self._visual_elapsed = 0.0
        self._scheduled_wake_seconds = 0.18
        self._animation_timer = QTimer(self)
        self._animation_timer.setSingleShot(True)
        self._animation_timer.setTimerType(Qt.PreciseTimer)
        self._animation_timer.setInterval(180)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._init_spritesheet_engine()
        self._idle_wake_timer = QTimer(self)
        self._idle_wake_timer.setSingleShot(True)
        self._idle_wake_timer.timeout.connect(self._advance_animation)
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

    def _init_spritesheet_engine(self) -> None:
        """Load the spritesheet manifest and initialize the animation engine."""
        requested = _get_pet_manifest_path(config.PET_SOURCE, config.PET_NAME)
        built_in = _get_pet_manifest_path("built-in", "owl-v2")
        for manifest_path in dict.fromkeys((requested, built_in)):
            try:
                manifest = load_manifest(manifest_path)
                manifest_dir = Path(manifest_path).parent
                image_path = (manifest_dir / manifest.spritesheet.file).resolve()
                if not image_path.is_relative_to(manifest_dir.resolve()):
                    raise ManifestError("spritesheet resolves outside the pet directory")
                if config.PET_SOURCE == "petdex":
                    manifest = apply_petdex_profile(manifest, image_path)
                ss = Spritesheet.from_manifest(
                    manifest_dir, manifest.spritesheet.file,
                    manifest.spritesheet.frame_width, manifest.spritesheet.frame_height,
                    manifest.spritesheet.columns, manifest.spritesheet.frame_count,
                    remove_magenta_matte=manifest.rendering.remove_magenta_matte,
                )
                if not ss.load():
                    raise ManifestError("spritesheet cannot be loaded")
            except ManifestError as exc:
                _logger.warning("Pet asset unavailable: %s", exc)
                continue
            self._spritesheet = ss
            self._animator = PetAnimator(manifest)
            self._spritesheet_available = True
            self._update_spritesheet_frame()
            return

    def _update_spritesheet_frame(self) -> None:
        """Cache the current frame from the animator for rendering."""
        if self._animator is None or self._spritesheet is None:
            return
        snap = self._animator.snapshot()
        self._spritesheet_frame = self._spritesheet.frame(snap.frame_index)

    @staticmethod
    def _normalize_pet_size(value: str) -> str:
        value = str(value).strip().lower()
        return value if value in _PET_SIZES else _DEFAULT_PET_SIZE

    def _apply_mode(self) -> None:
        if self._pet_enabled and self._spritesheet_available:
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

    def _spritesheet_draw_rect(self) -> QRectF:
        """Position the spritesheet frame using the manifest anchor point."""
        fw = self._spritesheet.frame_width
        fh = self._spritesheet.frame_height
        anchor = self._animator.anchor if self._animator else None
        ax = anchor.x if anchor else 0.5
        ay = anchor.y if anchor else 0.95
        rendering = self._animator.manifest.rendering
        scale = min(self.width() / fw, self.height() / fh) * rendering.scale
        draw_w = fw * scale
        draw_h = fh * scale
        anchor_x = self.rect().left() + self.rect().width() * ax
        baseline = rendering.baseline if rendering.baseline is not None else ay
        anchor_y = self.rect().top() + self.rect().height() * baseline
        return QRectF(
            anchor_x - draw_w * ax,
            anchor_y - draw_h * ay,
            draw_w,
            draw_h,
        )

    def _build_idle_hit_mask(self) -> QRegion:
        """Keep transparent window corners click-through while preserving the owl."""
        image = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform,
                              not self._animator.manifest.rendering.pixel_art)
        if self._spritesheet_available and not self._spritesheet_frame.isNull():
            draw_rect = self._spritesheet_draw_rect()
            # All poses share a single mask. No edge clipping or enter/leave
            # flicker when a tiny acknowledgement changes the silhouette.
            indices = {index for animation in self._animator.manifest.animations.values()
                       for index in animation.frames}
            for index in sorted(indices):
                frame = self._spritesheet.frame(index)
                painter.drawPixmap(draw_rect, frame, QRectF(frame.rect()))
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
        return region

    def _sync_hit_mask(self) -> None:
        if not self._pet_enabled or not self._spritesheet_available:
            return
        # Badges may extend beyond the silhouette; idle is the
        # long-lived state where transparent corners should pass clicks through.
        mask = (
            self._idle_hit_mask
            if self._effective_state() == "idle"
            else QRegion(self.rect())
        )
        if self.mask() != mask:
            self.setMask(mask)

    def _sync_animation_timer(self) -> None:
        self._animation_timer.stop()
        self._idle_wake_timer.stop()
        if not self.isVisible() or self._reduce_motion or self._is_dragging or self._menu_open:
            return
        if not self._pet_enabled or self._animator is None:
            if self._responding:
                self._scheduled_wake_seconds = 0.18
                self._animation_timer.start(180)
            return
        wake = self._animator.next_wake_seconds
        if self._effective_state() != "idle":
            # Task badges remain fluid even when the body is held in a calm pose.
            wake = min(wake, 0.04) if wake is not None else 0.04
        if wake is None:
            return
        milliseconds = max(16, math.ceil(wake * 1000))
        self._scheduled_wake_seconds = milliseconds / 1000
        if milliseconds > 250:
            self._idle_wake_timer.start(milliseconds)
        else:
            self._animation_timer.start(milliseconds)

    def set_pet_enabled(self, enabled: bool) -> None:
        self._pet_enabled_requested = bool(enabled)
        enabled = bool(enabled and self._spritesheet_available)
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
        self._reduce_motion_requested = bool(enabled)
        self._reduce_motion = bool(enabled or self._motion_preference.reduced)
        if self._reduce_motion:
            self._animation_phase = 0
            self._visual_elapsed = 0.0
            if self._spritesheet_available and self._animator is not None:
                self._sync_animator_state()
            self.setWindowOpacity(1.0)
        if not self._reduce_motion:
            self._sync_animator_state()
        self._last_animation_time = time.monotonic()
        self._sync_animation_timer()
        self.update()

    def _on_system_motion_changed(self, reduced: bool) -> None:
        self._motion_preference.reduced = reduced
        self.set_reduce_motion(self._reduce_motion_requested)

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

    def reload_pet(self) -> None:
        """Reload the spritesheet engine with the current config pet source/name."""
        anchor = self.geometry().bottomRight()
        self._animation_timer.stop()
        self._idle_wake_timer.stop()
        self._spritesheet = None
        self._animator = None
        self._spritesheet_available = False
        self._init_spritesheet_engine()
        self._pet_enabled = bool(self._pet_enabled_requested and self._spritesheet_available)
        self._sync_animator_state()
        self._last_animation_time = time.monotonic()
        self._apply_mode()
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
        if not self._pet_enabled or not self._spritesheet_available:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        # Translucent windows have no automatic opaque background erase.
        # Clear the previous pose before drawing exactly one current frame.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform,
                              not self._animator.manifest.rendering.pixel_art)

        scale = min(self.width() / 116, self.height() / 122)
        def px(value: float) -> int:
            return max(1, round(value * scale))

        state = self._effective_state()
        if self._spritesheet_frame and not self._spritesheet_frame.isNull():
            draw_rect = self._spritesheet_draw_rect()
            painter.drawPixmap(draw_rect, self._spritesheet_frame, QRectF(self._spritesheet_frame.rect()))

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
        if self._reduce_motion or not self.isVisible() or self._is_dragging:
            self._animation_timer.stop()
            return
        now = time.monotonic()
        delta = max(0.0, now - self._last_animation_time)
        self._last_animation_time = now
        # No fast-forward through gestures after sleep or a stalled GUI callback.
        delta = min(delta, max(0.25, self._scheduled_wake_seconds + 0.1))
        self._visual_elapsed += delta
        self._animation_phase = int(self._visual_elapsed / 0.18) % 96
        if not self._pet_enabled:
            if self._responding:
                self.setWindowOpacity(0.55 if self._animation_phase % 12 < 6 else 1.0)
        elif self._animator is not None:
            self._animator.tick(delta)
            self._update_spritesheet_frame()
        self.update()
        self._sync_animation_timer()

    def enterEvent(self, event) -> None:
        newly_entered = not self._hovered
        self._hovered = True
        if newly_entered and self._effective_state() == "idle" and not self._reduce_motion and not self._is_dragging:
            self._sync_animator_state()
            now = time.monotonic()
            if self._animator is not None and now - self._last_hover_reaction_time >= 0.8:
                self._animator.react()
                self._last_hover_reaction_time = now
                self._update_spritesheet_frame()
        self._sync_animation_timer()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self._effective_state() == "idle" and self._animator is not None:
            self._animator.settle_to("idle")
            self._update_spritesheet_frame()
        self._sync_animation_timer()
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event) -> None:
        self._last_animation_time = time.monotonic()
        self._sync_animator_state()
        self._sync_hit_mask()
        self._sync_animation_timer()
        if hasattr(self, "_track_timer") and self._follow_cursor_screen_enabled:
            self._track_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._animation_timer.stop()
        self._idle_wake_timer.stop()
        self._result_timer.stop()
        if hasattr(self, "_track_timer"):
            self._track_timer.stop()
        self._result_state = None
        self._hovered = False
        self._sync_animator_state()
        self._sync_hit_mask()
        super().hideEvent(event)

    # ── 响应状态脉冲 ───────────────────────────────────

    def set_responding(self, responding: bool) -> None:
        """设置 AI 生成状态。"""
        if responding and not self._responding:
            self._animation_phase = 0
            self._visual_elapsed = 0.0
        self._responding = responding
        if responding:
            self._result_timer.stop()
            self._result_state = None
        self._sync_animator_state()
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setWindowOpacity(1.0)
        self.setToolTip(self._state_tooltip())
        self.update()

    def show_result(self, succeeded: bool) -> None:
        """短暂显示本次请求结果，然后恢复空闲状态。"""
        self._animation_phase = 0
        self._visual_elapsed = 0.0
        self._result_state = "success" if succeeded else "error"
        if self._spritesheet_available and self._animator is not None:
            if self._effective_state() == self._result_state:
                self._last_animation_time = time.monotonic()
                self._animator.trigger_event(self._result_state)
                self._animator.tick(0)
                self._update_spritesheet_frame()
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
        self._sync_animator_state()
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_listening(self, listening: bool) -> None:
        """设置选区或截图捕获状态；生成状态始终优先。"""
        if listening and not self._listening:
            self._animation_phase = 0
            self._visual_elapsed = 0.0
        self._listening = listening
        self._sync_animator_state()
        self._sync_hit_mask()
        self._sync_animation_timer()
        self.setToolTip(self._state_tooltip())
        self.update()

    def set_speaking(self, speaking: bool) -> None:
        """设置语音生成或播放状态。"""
        if speaking and not self._speaking:
            self._animation_phase = 0
            self._visual_elapsed = 0.0
        self._speaking = speaking
        if speaking:
            self._result_timer.stop()
            self._result_state = None
        self._sync_animator_state()
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

    def _sync_animator_state(self) -> None:
        """Push the current effective state to the animator."""
        if not self._spritesheet_available or self._animator is None:
            return
        state = self._effective_state()
        if state == "idle" and self._hovered and not self._reduce_motion:
            state = "hover"
        previous = self._animator.current_state
        self._animator.set_state(state, restart=self._reduce_motion)
        if previous != self._animator.current_state:
            self._last_animation_time = time.monotonic()
        self._update_spritesheet_frame()

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
                    if self._animator is not None and self._effective_state() == "idle":
                        self._animator.set_state("idle", restart=True)
                        self._update_spritesheet_frame()
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
            self._last_animation_time = time.monotonic()
            self._sync_animator_state()
            self._sync_animation_timer()
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._menu_open = True
        self._sync_animation_timer()
        menu = self._create_context_menu()
        self._context_menu = menu

        def menu_closed():
            self._menu_open = False
            self._last_animation_time = time.monotonic()
            self._sync_animation_timer()
            menu.deleteLater()

        menu.closedSignal.connect(menu_closed)
        menu.exec_(event.globalPos())

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)

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
