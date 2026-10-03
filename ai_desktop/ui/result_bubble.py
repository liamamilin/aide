"""桌面宠物旁的轻量结果摘要气泡。"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QWidget,
)

from ai_desktop.ui.fluent import InfoBar, InfoBarIcon, InfoBarPosition, initialize


class ResultBubble(QWidget):
    """展示简短任务结果，不抢占当前应用焦点。"""

    activated = pyqtSignal()
    dismissed = pyqtSignal()

    _WIDTH = 296
    _HEIGHT = 96
    _POINTER = 0
    _GAP = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._kind = "success"
        self._anchor_side = "left"
        self._pressed = False
        self._activate_on_click = True
        initialize()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._setup_window()
        self._setup_content()
        self.refresh_theme()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("AI 桌面宠物结果摘要")

    def _setup_content(self) -> None:
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._content = None
        self._replace_bar("任务完成", "")

    def _replace_bar(self, title, summary):
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.hide()
            self._content.deleteLater()
        icon = {"success": InfoBarIcon.SUCCESS, "error": InfoBarIcon.ERROR,
                "action": InfoBarIcon.WARNING, "progress": InfoBarIcon.INFORMATION}[self._kind]
        self._content = InfoBar(icon, title, summary, Qt.Vertical, True, -1,
                                InfoBarPosition.NONE, self)
        self._content.installEventFilter(self)
        self._title = self._content.titleLabel
        self._summary = self._content.contentLabel
        self._summary.setWordWrap(True)
        self._title.setTextFormat(Qt.PlainText)
        self._summary.setTextFormat(Qt.PlainText)
        for label in (self._title, self._summary):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._close = self._content.closeButton
        self._close.setAccessibleName("关闭结果摘要")
        self._close.clicked.disconnect()
        self._close.clicked.connect(self.dismiss)
        self._layout.addWidget(self._content)
        # Reserve enough width for summaries; height follows the real InfoBar.
        self.setFixedWidth(self._WIDTH)
        self.setFixedHeight(max(self._HEIGHT, self._content.sizeHint().height()))

    @property
    def result_kind(self) -> str:
        return self._kind

    @staticmethod
    def summarize(text: str, fallback: str, limit: int = 96) -> str:
        summary = " ".join((text or "").split()) or fallback
        if len(summary) > limit:
            return summary[:limit].rstrip() + "…"
        return summary

    def show_result(
        self,
        kind: str,
        title: str,
        summary: str,
        anchor: QRect,
        *,
        timeout_ms: int = 7000,
        activate_on_click: bool = True,
    ) -> None:
        if kind not in {"success", "error", "action", "progress"}:
            raise ValueError(f"Unsupported result bubble kind: {kind}")
        self._kind = kind
        self._activate_on_click = activate_on_click
        cursor = Qt.PointingHandCursor if activate_on_click else Qt.ArrowCursor
        self.setCursor(cursor)
        self._replace_bar(title, summary)
        self._content.setCursor(cursor)
        self.position_near(anchor)
        self.show()
        self.raise_()
        self._hide_timer.start(max(1000, timeout_ms))

    def position_near(self, anchor: QRect) -> None:
        area = self._screen_geometry(anchor)
        left_x = anchor.left() - self._GAP - self.width()
        right_x = anchor.right() + 1 + self._GAP
        if left_x >= area.left():
            x = left_x
            side = "left"
        elif right_x + self.width() - 1 <= area.right():
            x = right_x
            side = "right"
        elif anchor.center().x() >= area.center().x():
            x = left_x
            side = "left"
        else:
            x = right_x
            side = "right"

        max_x = max(area.left(), area.right() - self.width() + 1)
        x = min(max(x, area.left()), max_x)
        y = anchor.center().y() - self.height() // 2
        max_y = max(area.top(), area.bottom() - self.height() + 1)
        y = min(max(y, area.top()), max_y)
        self._set_anchor_side(side)
        self.move(x, y)

    @staticmethod
    def _screen_geometry(anchor: QRect) -> QRect:
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        if screen is None:
            return QRect(anchor.x() - 320, anchor.y() - 100, 640, 480)
        return screen.availableGeometry()

    def _set_anchor_side(self, side: str) -> None:
        if side != self._anchor_side:
            self._anchor_side = side
            self._update_content_geometry()
            self.update()

    def _update_content_geometry(self) -> None:
        self._layout.activate()

    def refresh_theme(self) -> None:
        initialize()
        self.update()

    def _activate(self) -> None:
        if not self.isVisible():
            return
        if not self._activate_on_click:
            self.dismiss()
            return
        self._hide_timer.stop()
        self.hide()
        self.activated.emit()

    def dismiss(self) -> None:
        was_visible = self.isVisible()
        self._hide_timer.stop()
        self.hide()
        if was_visible:
            self.dismissed.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed = event.button() == Qt.LeftButton
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pressed and event.button() == Qt.LeftButton:
            self._pressed = False
            self._activate()
            return
        self._pressed = False
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._content:
            if event.type() == QEvent.MouseButtonPress:
                self._pressed = event.button() == Qt.LeftButton
            elif event.type() == QEvent.MouseButtonRelease:
                activate = self._pressed and event.button() == Qt.LeftButton
                self._pressed = False
                if activate:
                    self._activate()
                    return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self._hide_timer.stop()
        self._pressed = False
        super().hideEvent(event)
