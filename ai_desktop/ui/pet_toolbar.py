"""A small, non-activating action surface shared with the desktop pet."""

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout

from ai_desktop.ui.fluent import CaptionLabel, SimpleCardWidget, TransparentPushButton


class PetToolbar(SimpleCardWidget):
    activated = pyqtSignal(str)
    entered = pyqtSignal()
    left = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName("桌面宠物快捷操作")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        self.status = CaptionLabel(self)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        layout.addLayout(actions)
        self.buttons = []
        self._actions = []
        for index in range(3):
            button = TransparentPushButton(self)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, index=index: self._activate(index))
            actions.addWidget(button)
            self.buttons.append(button)

    def configure(self, status: str, actions: list[tuple[str, str]]) -> None:
        self.status.setText(status)
        self.status.setVisible(bool(status))
        self._actions = actions[:3]
        for index, button in enumerate(self.buttons):
            button.setVisible(index < len(self._actions))
            if index < len(self._actions):
                button.setText(self._actions[index][1])
                button.setAccessibleName(self._actions[index][1])
        self.adjustSize()

    def position_near(self, anchor: QRect, area: QRect) -> None:
        self.adjustSize()
        x = anchor.center().x() - self.width() // 2
        y = anchor.top() - self.height() - 8
        if y < area.top():
            y = anchor.bottom() + 9
        x = max(area.left(), min(x, area.right() - self.width() + 1))
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)

    def _activate(self, index: int) -> None:
        if index < len(self._actions):
            action = self._actions[index][0]
            self.hide()
            self.activated.emit(action)

    def enterEvent(self, event) -> None:
        self.entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.left.emit()
        super().leaveEvent(event)
