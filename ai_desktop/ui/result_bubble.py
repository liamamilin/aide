"""桌面宠物旁的轻量结果摘要气泡。"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.ui import theme


class ResultBubble(QWidget):
    """展示简短任务结果，不抢占当前应用焦点。"""

    activated = pyqtSignal()
    dismissed = pyqtSignal()

    _WIDTH = 296
    _HEIGHT = 96
    _POINTER = 10
    _GAP = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._kind = "success"
        self._anchor_side = "left"
        self._pressed = False
        self._activate_on_click = True
        self._colors = theme.current()
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
        self._content = QWidget(self)
        self._content.setCursor(Qt.PointingHandCursor)
        self._content.installEventFilter(self)

        layout = QHBoxLayout(self._content)
        layout.setContentsMargins(13, 9, 9, 9)
        layout.setSpacing(9)

        self._status = QLabel("✓")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setFixedSize(28, 28)
        self._status.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self._status, alignment=Qt.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 1, 0, 0)
        text_layout.setSpacing(3)
        self._title = QLabel("任务完成")
        self._title.setTextFormat(Qt.PlainText)
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._summary = QLabel("")
        self._summary.setTextFormat(Qt.PlainText)
        self._summary.setWordWrap(True)
        self._summary.setMaximumHeight(42)
        self._summary.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_layout.addWidget(self._title)
        text_layout.addWidget(self._summary)
        layout.addLayout(text_layout, stretch=1)

        self._close = QPushButton("×")
        self._close.setAccessibleName("关闭结果摘要")
        self._close.setToolTip("关闭")
        self._close.setCursor(Qt.ArrowCursor)
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.setFixedSize(24, 24)
        self._close.clicked.connect(self.dismiss)
        layout.addWidget(self._close, alignment=Qt.AlignTop)
        self._update_content_geometry()

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
        self._content.setCursor(cursor)
        self._title.setText(title)
        self._summary.setText(summary)
        self._status.setText(
            {"success": "✓", "error": "!", "action": "!", "progress": "…"}[kind]
        )
        self.refresh_theme()
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
        if self._anchor_side == "left":
            self._content.setGeometry(0, 0, self.width() - self._POINTER, self.height())
        else:
            self._content.setGeometry(
                self._POINTER, 0, self.width() - self._POINTER, self.height()
            )

    def refresh_theme(self) -> None:
        self._colors = theme.current()
        accent = {
            "success": self._colors.success,
            "error": self._colors.error,
            "action": "#F5A623" if self._colors.window == theme.LIGHT.window else "#FFB340",
            "progress": self._colors.accent,
        }[self._kind]
        self._content.setStyleSheet("background: transparent;")
        self._status.setStyleSheet(
            f"background: {accent}; color: white; border-radius: 14px; "
            "font-size: 16px; font-weight: bold;"
        )
        self._title.setStyleSheet(
            f"color: {self._colors.text}; background: transparent; "
            "font-size: 13px; font-weight: bold;"
        )
        self._summary.setStyleSheet(
            f"color: {self._colors.text_secondary}; background: transparent; "
            "font-size: 12px;"
        )
        self._close.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {self._colors.text_secondary}; font-size: 18px; }}"
            f"QPushButton:hover {{ color: {self._colors.text}; "
            f"background: {self._colors.button}; border-radius: 12px; }}"
        )
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pointer = self._POINTER
        if self._anchor_side == "left":
            body = QRectF(1, 1, self.width() - pointer - 3, self.height() - 4)
            tip = QPoint(self.width() - 1, self.height() // 2)
            base_x = round(body.right()) - 1
        else:
            body = QRectF(pointer + 2, 1, self.width() - pointer - 3, self.height() - 4)
            tip = QPoint(1, self.height() // 2)
            base_x = round(body.left()) + 1

        shadow = QPainterPath()
        shadow.addRoundedRect(body.translated(0, 2), 14, 14)
        painter.fillPath(shadow, QColor(8, 18, 38, 38))

        fill = QColor(self._colors.window)
        border = QColor(self._colors.border)
        body_path = QPainterPath()
        body_path.addRoundedRect(body, 14, 14)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawPath(body_path)

        tail = QPolygonF(
            [
                QPoint(base_x, self.height() // 2 - 8),
                tip,
                QPoint(base_x, self.height() // 2 + 8),
            ]
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(tail)
        painter.setPen(QPen(border, 1))
        painter.drawLine(tail[0], tail[1])
        painter.drawLine(tail[1], tail[2])
        painter.end()

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
