"""
屏幕悬浮按钮 —— 可拖拽，始终置顶，圆形图标，跟随屏幕切换，跨 Spaces
"""
import ctypes
import ctypes.util
import os

from PyQt5.QtCore import Qt, QPoint, QRectF, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QPainterPath, QCursor
from PyQt5.QtWidgets import QPushButton, QApplication, QMenu

_ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "图标.png")

_SIZE = 32


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

        # Two typed wrappers around the same objc_msgSend, avoiding runtime
        # argtypes switching which crashes on ARM64 macOS.
        SendId = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        SendBeh = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)

        msg_send_id = SendId(("objc_msgSend", objc))
        msg_send_beh = SendBeh(("objc_msgSend", objc))

        ns_win = msg_send_id(view_ptr, sel_window)
        if ns_win:
            # NSWindowCollectionBehaviorCanJoinAllSpaces = 1
            # (visible on all Spaces — mutually exclusive with MoveToActiveSpace)
            msg_send_beh(ns_win, sel_behavior, 1)
    except Exception:
        pass


class FloatButton(QPushButton):
    exit_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    about_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._is_dragging: bool = False
        self._init_ui()
        self._position_initial()
        self._start_screen_tracking()

    def _init_ui(self) -> None:
        self.setFixedSize(_SIZE, _SIZE)
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)

        if os.path.exists(_ICON_PATH):
            icon = _make_circular_icon(_ICON_PATH, _SIZE)
            self.setIcon(icon)
            self.setIconSize(self.size())
            self.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; border-radius: {_SIZE // 2}px; }}"
                f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.15); border-radius: {_SIZE // 2}px; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: rgba(0,122,255,0.85); border-radius: {_SIZE // 2}px; }}"
            )
            self.setText("AI")

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
        drag_pos = self._drag_pos
        self._drag_pos = None
        if drag_pos is not None:
            delta = event.globalPos() - drag_pos - self.frameGeometry().topLeft()
            if delta.manhattanLength() < 5:
                super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #ffffff; border: 1px solid #ccc; border-radius: 6px; padding: 4px 0; }"
            "QMenu::item { padding: 6px 24px; font-size: 12px; }"
            "QMenu::item:selected { background: #007AFF; color: white; border-radius: 4px; }"
        )
        hide_action = menu.addAction("隐藏悬浮球")
        hide_action.triggered.connect(self.hide_requested.emit)
        menu.addSeparator()
        about_action = menu.addAction("关于 AI 桌面助手")
        about_action.triggered.connect(self.about_requested.emit)
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.exit_requested.emit)
        menu.exec_(event.globalPos())
