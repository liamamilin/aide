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
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(6)
        heading = QHBoxLayout()
        heading.setSpacing(7)
        label = QLabel("快捷动作")
        label.setStyleSheet(styles.LABEL_BOLD)
        label.setToolTip("先在输入框输入/粘贴文字，或在其他应用选中文字后按 ⌘⌃L")
        heading.addWidget(label)
        heading.addStretch()
        self._mode_combo = QComboBox()
        self._mode_combo.setStyleSheet(styles.COMBO_BOX)
        self._mode_combo.setFixedWidth(106)
        self._mode_combo.addItem("新对话", "new")
        self._mode_combo.addItem("当前对话", "current")
        self._mode_combo.setToolTip("选择动作结果所属的对话")
        heading.addWidget(self._mode_combo)
        collapse = QPushButton("收起")
        collapse.setStyleSheet(styles.SECONDARY_BUTTON)
        collapse.setToolTip("收起快捷动作面板")
        collapse.clicked.connect(self._collapse)
        heading.addWidget(collapse)
        root.addLayout(heading)

        context = QHBoxLayout()
        context.setSpacing(8)
        self._material_hint = QLabel()
        self._material_hint.setWordWrap(True)
        self._material_hint.setStyleSheet(styles.LABEL_SECONDARY)
        context.addWidget(self._material_hint, stretch=1)
        hint = QLabel("数字键选择 · Enter 执行")
        hint.setStyleSheet(styles.LABEL_SECONDARY)
        hint.setToolTip("也可用 ←→ 切换，Esc 收起")
        context.addWidget(hint)
        root.addLayout(context)
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
            button.setToolTip(action.name)
            button.clicked.connect(
                lambda checked, selected=index: self._trigger(selected)
            )
            self._button_row.addWidget(button, stretch=1)
            self._buttons.append(button)
        self._selected_index = min(self._selected_index, max(0, len(self._buttons) - 1))
        self._update_material_state()
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
        self._update_material_state()
        self._refresh_selection()
        self.show()
        if self._material.strip():
            self.setFocus(Qt.ShortcutFocusReason)

    def set_material(self, material: str) -> None:
        """Keep the panel state in sync while the user types or pastes material."""
        self._material = material
        self._update_material_state()

    def _update_material_state(self) -> None:
        ready = bool(self._material.strip())
        for button in self._buttons:
            button.setEnabled(ready)
        if ready:
            self._material_hint.setText(
                f"已准备 {len(self._material.strip())} 个字符 · 可连续处理"
            )
        else:
            self._material_hint.setText(
                "先在下方输入或粘贴文字；也可以在其他应用选中文字后按 ⌘⌃L。"
            )

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

    def _collapse(self) -> None:
        self.hide()
        self.cancelled.emit()

    def _move(self, offset: int) -> None:
        if not self._actions:
            return
        self._selected_index = (self._selected_index + offset) % len(self._actions)
        self._refresh_selection()

    def _trigger(self, index: int) -> None:
        if not 0 <= index < len(self._actions):
            return
        if not self._material.strip():
            return
        action = self._actions[index]
        self._selected_index = index
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
