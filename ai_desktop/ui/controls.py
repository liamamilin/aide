"""Small polished controls shared by the desktop UI."""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QComboBox, QListView, QSizePolicy

from ai_desktop.ui.theme import current


class PolishedComboBox(QComboBox):
    """A compact macOS-style combo box with predictable rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setFrame(False)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        popup = QListView(self)
        popup.setUniformItemSizes(True)
        popup.setTextElideMode(Qt.ElideRight)
        popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setView(popup)
        self._apply_popup_theme()

    def _apply_popup_theme(self) -> None:
        c = current()
        self.view().setStyleSheet(
            f"QListView {{ background: {c.surface_elevated}; color: {c.text}; "
            f"border: 1px solid {c.border}; border-radius: 10px; padding: 5px; outline: none; }}"
            f"QListView::item {{ min-height: 30px; padding: 2px 10px; border-radius: 6px; }}"
            f"QListView::item:hover {{ background: {c.button_hover}; }}"
            f"QListView::item:selected {{ background: {c.accent}; color: white; }}"
        )

    def paintEvent(self, event) -> None:
        c = current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        if not self.isEnabled():
            background = QColor(c.disabled)
            foreground = QColor(c.disabled_text)
        else:
            background = QColor(c.button_hover if self._hovered else c.surface_muted)
            foreground = QColor(c.text)
        border = QColor(c.focus_ring if self.hasFocus() else c.separator)
        painter.setPen(QPen(border, 1.5 if self.hasFocus() else 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 8, 8)

        text_rect = self.rect().adjusted(11, 0, -31, 0)
        text = QFontMetrics(self.font()).elidedText(self.currentText(), Qt.ElideRight, text_rect.width())
        painter.setPen(foreground)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        center_x = self.width() - 16
        center_y = self.height() / 2
        painter.setPen(QPen(QColor(c.text_secondary), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(center_x - 3, int(center_y - 1), center_x, int(center_y + 2))
        painter.drawLine(center_x, int(center_y + 2), center_x + 3, int(center_y - 1))

    def showPopup(self) -> None:
        self._apply_popup_theme()
        metrics = QFontMetrics(self.font())
        content_width = max(
            (metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())),
            default=self.width(),
        )
        popup_width = max(self.width(), min(content_width + 42, 360))
        self.view().setMinimumWidth(popup_width)
        self.view().setMaximumWidth(popup_width)
        super().showPopup()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
