"""Compact keyboard-accessible quick-action bar."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.services.action_service import Action
from ai_desktop.ui import styles


class ActionPanel(QWidget):
    action_selected = pyqtSignal(str, str, str)
    cancelled = pyqtSignal()

    def __init__(self, actions: list[Action], parent=None):
        super().__init__(parent)
        self._actions = list(actions)
        self._material = ""
        self._selected_index = 0
        self._buttons: list[QPushButton] = []
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(styles.AGENT_LIST_BAR)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)
        heading = QHBoxLayout()
        label = QLabel("快捷动作")
        label.setStyleSheet(styles.LABEL_BOLD)
        label.setToolTip("执行前可选择新建对话或在当前对话继续")
        heading.addWidget(label)
        heading.addStretch()
        self._mode_combo = QComboBox()
        self._mode_combo.setStyleSheet(styles.COMBO_BOX)
        self._mode_combo.addItem("新建专用对话", "new")
        self._mode_combo.addItem("在当前对话继续", "current")
        self._mode_combo.setToolTip("选择动作结果所属的对话")
        heading.addWidget(self._mode_combo)
        hint = QLabel("1–4 / ←→ 选择 · Enter 执行 · Esc 自由提问")
        hint.setStyleSheet(styles.LABEL_SECONDARY)
        heading.addWidget(hint)
        root.addLayout(heading)
        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(6)
        root.addLayout(self._button_row)
        self.refresh_actions(actions)
        self.hide()

    def refresh_actions(self, actions: list[Action]) -> None:
        self._actions = list(actions)[:4]
        while self._button_row.count():
            item = self._button_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._buttons = []
        for index, action in enumerate(self._actions):
            button = QPushButton(f"{index + 1}  {action.name}")
            button.setStyleSheet(styles.SECONDARY_BUTTON)
            button.clicked.connect(
                lambda checked, selected=index: self._trigger(selected)
            )
            self._button_row.addWidget(button, stretch=1)
            self._buttons.append(button)
        self._selected_index = min(self._selected_index, max(0, len(self._buttons) - 1))
        self._refresh_selection()

    def show_for_material(
        self,
        material: str,
        selected_id: str | None = None,
        *,
        has_conversation: bool = False,
        mode: str = "new",
    ) -> None:
        self._material = material
        selected_mode = mode if has_conversation and mode == "current" else "new"
        self._mode_combo.setCurrentIndex(self._mode_combo.findData(selected_mode))
        self._mode_combo.setEnabled(has_conversation)
        self._mode_combo.setToolTip(
            "选择动作结果所属的对话"
            if has_conversation
            else "当前尚无已保存对话，执行后将新建专用对话"
        )
        if selected_id:
            index = next(
                (i for i, action in enumerate(self._actions) if action.id == selected_id),
                0,
            )
            self._selected_index = index
        else:
            self._selected_index = 0
        self._refresh_selection()
        self.show()
        self.setFocus(Qt.ShortcutFocusReason)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.cancelled.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self._move(-1)
            event.accept()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Down):
            self._move(1)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._trigger(self._selected_index)
            event.accept()
            return
        number = event.key() - Qt.Key_1
        if 0 <= number < len(self._actions):
            self._trigger(number)
            event.accept()
            return
        super().keyPressEvent(event)

    def _move(self, offset: int) -> None:
        if not self._actions:
            return
        self._selected_index = (self._selected_index + offset) % len(self._actions)
        self._refresh_selection()

    def _trigger(self, index: int) -> None:
        if not 0 <= index < len(self._actions):
            return
        action = self._actions[index]
        self._selected_index = index
        self.hide()
        self.action_selected.emit(
            action.id,
            self._material,
            str(self._mode_combo.currentData() or "new"),
        )

    def _refresh_selection(self) -> None:
        for index, button in enumerate(self._buttons):
            button.setStyleSheet(
                styles.BUTTON_PRIMARY
                if index == self._selected_index
                else styles.SECONDARY_BUTTON
            )
