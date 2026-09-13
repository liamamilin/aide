"""Quick-action configuration dialog."""

from dataclasses import replace

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.services.action_service import MAX_ACTION_NAME, Action, validate_action
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.ui import styles
from ai_desktop.ui.frameless_mixin import FramelessDragMixin


class ActionSettingsDialog(FramelessDragMixin, QDialog):
    actions_saved = pyqtSignal(list)

    def __init__(
        self,
        actions: list[Action],
        agents: list,
        profiles: list[ModelProfile],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._setup_drag(40)
        self._actions = list(actions)
        self._agents = list(agents)
        self._profiles = list(profiles)
        self._rows: dict[str, dict[str, QWidget]] = {}
        self._setup_window()
        self._setup_ui()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(720, 330)
        self.resize(780, 360)
        self.setStyleSheet(styles.DIALOG_BASE)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(styles.TITLE_BAR)
        title_layout = QHBoxLayout(title)
        title_layout.setContentsMargins(12, 0, 8, 0)
        heading = QLabel("快捷动作设置")
        heading.setStyleSheet(styles.LABEL_BOLD)
        title_layout.addWidget(heading)
        title_layout.addStretch()
        close = QPushButton("×")
        close.setFixedSize(24, 24)
        close.setStyleSheet(styles.CLOSE_BUTTON)
        close.clicked.connect(self.reject)
        title_layout.addWidget(close)
        root.addWidget(title)

        body = QWidget()
        body.setStyleSheet(styles.DIALOG_BODY)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(10)
        detail = QLabel("可重命名、隐藏、调整数字快捷顺序，并为动作指定 Agent 和模型配置。")
        detail.setStyleSheet(styles.LABEL_SECONDARY)
        body_layout.addWidget(detail)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for column, text in enumerate(("动作名称", "Agent", "模型配置", "数字快捷顺序", "显示")):
            label = QLabel(text)
            label.setStyleSheet(styles.LABEL_BOLD)
            grid.addWidget(label, 0, column)

        for row, action in enumerate(self._actions, start=1):
            name = QLineEdit(action.name)
            name.setMaxLength(MAX_ACTION_NAME)
            name.setStyleSheet(styles.FORM_INPUT)
            name.setObjectName(f"action_name_{action.id}")

            agent = QComboBox()
            agent.setStyleSheet(styles.COMBO_BOX)
            for item in self._agents:
                agent.addItem(f"{item.icon} {item.name}", item.id)
            if agent.findData(action.agent_id) < 0:
                agent.addItem(f"已删除：{action.agent_id}", action.agent_id)
            agent.setCurrentIndex(max(0, agent.findData(action.agent_id)))

            profile = QComboBox()
            profile.setStyleSheet(styles.COMBO_BOX)
            profile.addItem("继承 Agent / 全局", None)
            for item in self._profiles:
                profile.addItem(item.name, item.id)
            if action.profile_id and profile.findData(action.profile_id) < 0:
                profile.addItem(f"已删除：{action.profile_id}", action.profile_id)
            profile.setCurrentIndex(max(0, profile.findData(action.profile_id)))

            pinned = QComboBox()
            pinned.setStyleSheet(styles.COMBO_BOX)
            pinned.addItem("不绑定", None)
            for index in range(4):
                pinned.addItem(str(index + 1), index)
            pinned.setCurrentIndex(max(0, pinned.findData(action.pinned_order)))

            enabled = QCheckBox()
            enabled.setChecked(action.enabled)
            enabled.setStyleSheet(styles.LABEL)

            for column, widget in enumerate((name, agent, profile, pinned, enabled)):
                grid.addWidget(widget, row, column)
            self._rows[action.id] = {
                "name": name,
                "agent": agent,
                "profile": profile,
                "pinned": pinned,
                "enabled": enabled,
            }
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        body_layout.addLayout(grid)
        body_layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setStyleSheet(styles.CANCEL_BUTTON)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存")
        save.setStyleSheet(styles.SAVE_BUTTON)
        save.clicked.connect(self._on_save)
        buttons.addWidget(save)
        body_layout.addLayout(buttons)
        root.addWidget(body)

    def configured_actions(self) -> list[Action]:
        configured: list[Action] = []
        for action in self._actions:
            row = self._rows[action.id]
            configured.append(
                validate_action(
                    replace(
                        action,
                        name=row["name"].text().strip(),
                        agent_id=str(row["agent"].currentData()),
                        profile_id=row["profile"].currentData(),
                        pinned_order=row["pinned"].currentData(),
                        enabled=row["enabled"].isChecked(),
                        updated_at=0,
                    )
                )
            )
        return configured

    def _on_save(self) -> None:
        try:
            actions = self.configured_actions()
        except ValueError as exc:
            QMessageBox.warning(self, "输入错误", str(exc))
            return
        pinned = [item.pinned_order for item in actions if item.pinned_order is not None]
        if len(pinned) != len(set(pinned)):
            QMessageBox.warning(self, "输入错误", "数字快捷顺序不能重复。")
            return
        self.actions_saved.emit(actions)
        self.accept()
