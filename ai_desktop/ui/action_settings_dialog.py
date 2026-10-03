"""Quick-action configuration dialog."""

from dataclasses import replace

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ai_desktop.services.action_service import MAX_ACTION_NAME, Action, validate_action
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.ui.fluent import (
    CaptionLabel,
    PrimaryPushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    dialog_title,
)
from ai_desktop.ui.fluent import CheckBox as QCheckBox
from ai_desktop.ui.fluent import ComboBox as QComboBox
from ai_desktop.ui.fluent import FluentDialog as QDialog
from ai_desktop.ui.fluent import LineEdit as QLineEdit
from ai_desktop.ui.fluent import MessageBox as QMessageBox
from ai_desktop.ui.fluent import PushButton as QPushButton
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
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(720, 330)
        self.resize(780, 360)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(dialog_title(self, '快捷动作设置'))

        body = SimpleCardWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(10)
        detail = CaptionLabel("可重命名、隐藏、调整数字快捷顺序，并为动作指定 Agent 和模型配置。")
        body_layout.addWidget(detail)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for column, text in enumerate(("动作名称", "Agent", "模型配置", "数字快捷顺序", "显示")):
            label = StrongBodyLabel(text)
            grid.addWidget(label, 0, column)

        for row, action in enumerate(self._actions, start=1):
            name = QLineEdit()
            name.setText(action.name)
            name.setMaxLength(MAX_ACTION_NAME)
            name.setObjectName(f"action_name_{action.id}")

            agent = QComboBox()
            for item in self._agents:
                agent.addItem(f"{item.icon} {item.name}", item.id)
            if agent.findData(action.agent_id) < 0:
                agent.addItem(f"已删除：{action.agent_id}", action.agent_id)
            agent.setCurrentIndex(max(0, agent.findData(action.agent_id)))

            profile = QComboBox()
            profile.addItem("继承 Agent / 全局", None)
            for item in self._profiles:
                profile.addItem(item.name, item.id)
            if action.profile_id and profile.findData(action.profile_id) < 0:
                profile.addItem(f"已删除：{action.profile_id}", action.profile_id)
            profile.setCurrentIndex(max(0, profile.findData(action.profile_id)))

            pinned = QComboBox()
            pinned.addItem("不绑定", None)
            for index in range(4):
                pinned.addItem(str(index + 1), index)
            pinned.setCurrentIndex(max(0, pinned.findData(action.pinned_order)))

            enabled = QCheckBox()
            enabled.setChecked(action.enabled)

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
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = PrimaryPushButton("保存")
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
