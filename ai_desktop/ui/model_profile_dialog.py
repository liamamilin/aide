"""模型配置管理界面。"""

from dataclasses import replace

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ai_desktop import config
from ai_desktop.llm.thinking import ThinkMode, normalize_think
from ai_desktop.services.model_profiles import ModelProfile, validate_profile
from ai_desktop.ui.fluent import BodyLabel as QLabel
from ai_desktop.ui.fluent import (
    CaptionLabel,
    PrimaryPushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    TransparentPushButton,
    dialog_title,
)
from ai_desktop.ui.fluent import CheckBox as QCheckBox
from ai_desktop.ui.fluent import ComboBox as QComboBox
from ai_desktop.ui.fluent import DoubleSpinBox as QDoubleSpinBox
from ai_desktop.ui.fluent import FluentDialog as QDialog
from ai_desktop.ui.fluent import LineEdit as QLineEdit
from ai_desktop.ui.fluent import MessageBox as QMessageBox
from ai_desktop.ui.fluent import PushButton as QPushButton
from ai_desktop.ui.fluent import ScrollArea as QScrollArea
from ai_desktop.ui.fluent import SpinBox as QSpinBox
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.ui.thinking_selector import ThinkingSelector


class ModelProfileDialog(FramelessDragMixin, QDialog):
    profiles_saved = pyqtSignal(list)

    def __init__(self, profiles: list[ModelProfile], models: list[str], parent=None, *,
                 global_model="", base_url="", model_versions=None):
        super().__init__(parent)
        self._profiles = list(profiles)
        self._models = list(dict.fromkeys(models))
        self._thinking_context = dict(global_model=global_model, base_url=base_url, model_versions=model_versions)
        self._setup_drag(40)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(480, 360)
        self.resize(520, 420)
        self._setup_ui()
        self._refresh()

    @property
    def profiles(self) -> list[ModelProfile]:
        return list(self._profiles)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(dialog_title(self, '模型配置'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(6)
        self._layout.addStretch()
        scroll.setWidget(self._container)
        root.addWidget(scroll)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        add = QPushButton("＋ 新增配置")
        add.clicked.connect(self._on_add)
        bl.addWidget(add)
        bl.addStretch()
        hint = CaptionLabel("未填写的参数会继承全局设置")
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
            self._layout.insertWidget(0, empty)
            return
        for profile in self._profiles:
            row = SimpleCardWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 5, 8, 5)
            name = StrongBodyLabel(profile.name)
            layout.addWidget(name)
            summary = CaptionLabel(self._summary(profile))
            layout.addWidget(summary, stretch=1)
            edit = TransparentPushButton("编辑")
            edit.clicked.connect(lambda checked, item=profile: self._on_edit(item))
            layout.addWidget(edit)
            delete = TransparentPushButton("删除")
            delete.clicked.connect(lambda checked, item=profile: self._on_delete(item))
            layout.addWidget(delete)
            self._layout.insertWidget(self._layout.count() - 1, row)

    @staticmethod
    def _summary(profile: ModelProfile) -> str:
        values = [profile.model or "继承模型"]
        thinking = normalize_think(profile.think, allow_inherit=True)
        if thinking.mode != ThinkMode.INHERIT:
            values.append(f"思考：{thinking.label}")
        if profile.temperature is not None:
            values.append(f"温度 {profile.temperature:g}")
        if profile.num_predict is not None:
            values.append(f"输出 {profile.num_predict}")
        return " · ".join(values)

    def _on_add(self) -> None:
        profile = ModelProfile(self._next_id(), "", updated_at=0)
        dialog = _ModelProfileEditDialog("新增模型配置", profile, self._models, self, **self._thinking_context)
        if dialog.exec_() == QDialog.Accepted:
            self._profiles.append(dialog.profile())
            self._profiles.sort(key=lambda item: (item.name.casefold(), item.id))
            self._refresh()
            self.profiles_saved.emit(self.profiles)

    def _on_edit(self, profile: ModelProfile) -> None:
        dialog = _ModelProfileEditDialog("编辑模型配置", profile, self._models, self, **self._thinking_context)
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
    def __init__(self, title: str, profile: ModelProfile, models: list[str], parent=None, *,
                 global_model="", base_url="", model_versions=None):
        super().__init__(parent)
        self._source = profile
        self._global_model = global_model
        self._base_url = base_url or config.OLLAMA_BASE_URL
        self._model_versions = dict(model_versions or {})
        self._setup_drag(36)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumSize(460, 460)
        self.resize(520, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(dialog_title(self, title))

        body = SimpleCardWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(9)

        form.addWidget(QLabel("配置名称"))
        self._name = QLineEdit()
        self._name.setText(profile.name)
        form.addWidget(self._name)

        form.addWidget(QLabel("模型"))
        self._model = QComboBox()
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
        self._think = ThinkingSelector(profile.think, self, allow_inherit=True)
        form.addWidget(self._think)
        self._model.currentIndexChanged.connect(self._thinking_model_changed)
        self._thinking_model_changed()

        self._temperature_enabled = QCheckBox("覆盖温度")
        self._temperature_enabled.setChecked(profile.temperature is not None)
        form.addWidget(self._temperature_enabled)
        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(2)
        self._temperature.setValue(profile.temperature if profile.temperature is not None else 0.7)
        self._temperature.setEnabled(self._temperature_enabled.isChecked())
        self._temperature_enabled.toggled.connect(self._temperature.setEnabled)
        form.addWidget(self._temperature)

        self._tokens_enabled = QCheckBox("覆盖输出上限")
        self._tokens_enabled.setChecked(profile.num_predict is not None)
        form.addWidget(self._tokens_enabled)
        self._tokens = QSpinBox()
        self._tokens.setRange(1, 999_999)
        self._tokens.setValue(profile.num_predict if profile.num_predict is not None else 20_480)
        self._tokens.setEnabled(self._tokens_enabled.isChecked())
        self._tokens_enabled.toggled.connect(self._tokens.setEnabled)
        form.addWidget(self._tokens)
        form.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = PrimaryPushButton("保存")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        footer = QWidget(self)
        footer.setLayout(buttons)
        buttons.setContentsMargins(16, 12, 16, 16)
        root.addWidget(footer)

    def _thinking_model_changed(self):
        model = self._model.currentData() or self._global_model
        self._think.set_model(self._base_url, model, self._model_versions.get(model, ""))

    def done(self, result):
        self._think.stop()
        super().done(result)

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
