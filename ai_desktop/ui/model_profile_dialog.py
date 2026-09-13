"""模型配置管理界面。"""

from dataclasses import replace

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ai_desktop.services.model_profiles import ModelProfile, validate_profile
from ai_desktop.ui import styles
from ai_desktop.ui.frameless_mixin import FramelessDragMixin


class ModelProfileDialog(FramelessDragMixin, QDialog):
    profiles_saved = pyqtSignal(list)

    def __init__(self, profiles: list[ModelProfile], models: list[str], parent=None):
        super().__init__(parent)
        self._profiles = list(profiles)
        self._models = list(dict.fromkeys(models))
        self._setup_drag(40)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(480, 360)
        self.resize(520, 420)
        self.setStyleSheet(styles.DIALOG_BASE)
        self._setup_ui()
        self._refresh()

    @property
    def profiles(self) -> list[ModelProfile]:
        return list(self._profiles)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)
        tl.addWidget(QLabel("模型配置"))
        tl.addStretch()
        close = QPushButton("×")
        close.setFixedSize(24, 24)
        close.setStyleSheet(styles.CLOSE_BUTTON)
        close.clicked.connect(self.accept)
        tl.addWidget(close)
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(styles.SCROLL_AREA)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(6)
        self._layout.addStretch()
        scroll.setWidget(self._container)
        root.addWidget(scroll)

        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(styles.AGENT_LIST_BAR)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        add = QPushButton("＋ 新增配置")
        add.setStyleSheet(styles.ADD_AGENT_BUTTON)
        add.clicked.connect(self._on_add)
        bl.addWidget(add)
        bl.addStretch()
        hint = QLabel("未填写的参数会继承全局设置")
        hint.setStyleSheet(styles.LABEL_SECONDARY)
        bl.addWidget(hint)
        root.addWidget(bar)

    def _refresh(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._profiles:
            empty = QLabel("尚未创建模型配置")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(styles.EMPTY_STATE)
            self._layout.insertWidget(0, empty)
            return
        for profile in self._profiles:
            row = QWidget()
            row.setStyleSheet(styles.TRANSPARENT)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 5, 8, 5)
            name = QLabel(profile.name)
            name.setStyleSheet(styles.LABEL_BOLD)
            layout.addWidget(name)
            summary = QLabel(self._summary(profile))
            summary.setStyleSheet(styles.LABEL_SECONDARY)
            layout.addWidget(summary, stretch=1)
            edit = QPushButton("编辑")
            edit.setStyleSheet(styles.AGENT_EDIT_BUTTON)
            edit.clicked.connect(lambda checked, item=profile: self._on_edit(item))
            layout.addWidget(edit)
            delete = QPushButton("删除")
            delete.setStyleSheet(styles.DELETE_BUTTON)
            delete.clicked.connect(lambda checked, item=profile: self._on_delete(item))
            layout.addWidget(delete)
            self._layout.insertWidget(self._layout.count() - 1, row)

    @staticmethod
    def _summary(profile: ModelProfile) -> str:
        values = [profile.model or "继承模型"]
        if profile.think is not None:
            values.append("思考开" if profile.think else "思考关")
        if profile.temperature is not None:
            values.append(f"温度 {profile.temperature:g}")
        if profile.num_predict is not None:
            values.append(f"输出 {profile.num_predict}")
        return " · ".join(values)

    def _on_add(self) -> None:
        profile = ModelProfile(self._next_id(), "", updated_at=0)
        dialog = _ModelProfileEditDialog("新增模型配置", profile, self._models, self)
        if dialog.exec_() == QDialog.Accepted:
            self._profiles.append(dialog.profile())
            self._profiles.sort(key=lambda item: (item.name.casefold(), item.id))
            self._refresh()
            self.profiles_saved.emit(self.profiles)

    def _on_edit(self, profile: ModelProfile) -> None:
        dialog = _ModelProfileEditDialog("编辑模型配置", profile, self._models, self)
        if dialog.exec_() == QDialog.Accepted:
            index = self._profiles.index(profile)
            self._profiles[index] = dialog.profile()
            self._profiles.sort(key=lambda item: (item.name.casefold(), item.id))
            self._refresh()
            self.profiles_saved.emit(self.profiles)

    def _on_delete(self, profile: ModelProfile) -> None:
        answer = QMessageBox.question(
            self,
            "删除模型配置",
            f"删除“{profile.name}”？使用它的 Agent 将自动继承全局设置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._profiles.remove(profile)
        self._refresh()
        self.profiles_saved.emit(self.profiles)

    def _next_id(self) -> str:
        existing = {profile.id for profile in self._profiles}
        index = 1
        while f"profile_{index}" in existing:
            index += 1
        return f"profile_{index}"


class _ModelProfileEditDialog(FramelessDragMixin, QDialog):
    def __init__(self, title: str, profile: ModelProfile, models: list[str], parent=None):
        super().__init__(parent)
        self._source = profile
        self._setup_drag(36)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(420, 390)
        self.resize(440, 420)
        self.setStyleSheet(styles.DIALOG_BASE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        title_bar = QWidget()
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(12, 0, 8, 0)
        tl.addWidget(QLabel(title))
        tl.addStretch()
        close = QPushButton("×")
        close.setFixedSize(24, 24)
        close.setStyleSheet(styles.CLOSE_BUTTON)
        close.clicked.connect(self.reject)
        tl.addWidget(close)
        root.addWidget(title_bar)

        body = QWidget()
        body.setStyleSheet(styles.DIALOG_BODY)
        form = QVBoxLayout(body)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(9)

        form.addWidget(QLabel("配置名称"))
        self._name = QLineEdit(profile.name)
        self._name.setStyleSheet(styles.FORM_INPUT)
        form.addWidget(self._name)

        form.addWidget(QLabel("模型"))
        self._model = QComboBox()
        self._model.setStyleSheet(styles.COMBO_BOX)
        self._model.addItem("继承全局模型", None)
        known_models = list(dict.fromkeys(models))
        if profile.model and profile.model not in known_models:
            self._model.addItem(f"{profile.model}（当前不可用）", profile.model)
        for model in known_models:
            self._model.addItem(model, model)
        model_index = self._model.findData(profile.model)
        self._model.setCurrentIndex(max(0, model_index))
        form.addWidget(self._model)

        form.addWidget(QLabel("思考模式"))
        self._think = QComboBox()
        self._think.setStyleSheet(styles.COMBO_BOX)
        self._think.addItem("继承全局设置", None)
        self._think.addItem("开启", True)
        self._think.addItem("关闭", False)
        self._think.setCurrentIndex(max(0, self._think.findData(profile.think)))
        form.addWidget(self._think)

        self._temperature_enabled = QCheckBox("覆盖温度")
        self._temperature_enabled.setChecked(profile.temperature is not None)
        form.addWidget(self._temperature_enabled)
        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(2)
        self._temperature.setValue(profile.temperature if profile.temperature is not None else 0.7)
        self._temperature.setStyleSheet(f"QDoubleSpinBox {{ {styles.FORM_WIDGET} }}")
        self._temperature.setEnabled(self._temperature_enabled.isChecked())
        self._temperature_enabled.toggled.connect(self._temperature.setEnabled)
        form.addWidget(self._temperature)

        self._tokens_enabled = QCheckBox("覆盖输出上限")
        self._tokens_enabled.setChecked(profile.num_predict is not None)
        form.addWidget(self._tokens_enabled)
        self._tokens = QSpinBox()
        self._tokens.setRange(1, 999_999)
        self._tokens.setValue(profile.num_predict if profile.num_predict is not None else 20_480)
        self._tokens.setStyleSheet(f"QSpinBox {{ {styles.FORM_WIDGET} }}")
        self._tokens.setEnabled(self._tokens_enabled.isChecked())
        self._tokens_enabled.toggled.connect(self._tokens.setEnabled)
        form.addWidget(self._tokens)
        form.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setStyleSheet(styles.CANCEL_BUTTON)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("保存")
        save.setStyleSheet(styles.SAVE_BUTTON)
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        form.addLayout(buttons)
        root.addWidget(body)

    def profile(self) -> ModelProfile:
        return validate_profile(
            replace(
                self._source,
                name=self._name.text().strip(),
                model=self._model.currentData(),
                think=self._think.currentData(),
                temperature=self._temperature.value() if self._temperature_enabled.isChecked() else None,
                num_predict=self._tokens.value() if self._tokens_enabled.isChecked() else None,
                updated_at=0,
            )
        )

    def _save(self) -> None:
        try:
            self.profile()
        except ValueError as exc:
            QMessageBox.warning(self, "配置无效", str(exc))
            return
        self.accept()
