"""Default Fluent controls populated from the selected model's /api/show."""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from ai_desktop.llm.service_checks import AsyncServiceChecks, normalize_service_url
from ai_desktop.llm.thinking import (
    ThinkingCapability,
    ThinkMode,
    ThinkSetting,
    cache_thinking,
    load_cached_thinking,
    normalize_think,
)
from ai_desktop.ui.fluent import CaptionLabel, ComboBox


class ThinkingSelector(QWidget):
    def __init__(self, setting=None, parent=None, *, allow_inherit=False):
        super().__init__(parent)
        self.allow_inherit = allow_inherit
        self.capability = ThinkingCapability()
        self._identity = None
        self._sequence = None
        self._checks = AsyncServiceChecks(self)
        self._checks.model_capability_checked.connect(self._checked)
        self.combo = ComboBox(self)
        self.hint = CaptionLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.hint)
        self._fill(normalize_think(setting, allow_inherit=allow_inherit))
        self.combo.currentIndexChanged.connect(self._selection_changed)
        self.destroyed.connect(self._checks.cancel_all)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)

    def currentData(self):
        return self.combo.currentData()

    def set_setting(self, setting):
        self._fill(normalize_think(setting, allow_inherit=self.allow_inherit))

    def set_model(self, base_url, model, version=''):
        identity = (normalize_service_url(base_url), str(model or ''), str(version or ''))
        if identity == self._identity:
            return
        self._refresh_timer.stop()
        self._checks.cancel_model_capability()
        self._sequence = None
        self._identity = identity
        self.capability = load_cached_thinking(*identity) if identity[1] else ThinkingCapability()
        self._fill(self.currentData())
        if identity[1]:
            self._refresh_timer.start(150)
        else:
            self.hint.setText('尚未选择模型；仅使用模型默认或继承设置。')

    def _refresh(self):
        if self._identity is None or not self._identity[1]:
            return
        self._sequence = self._checks.check_model_capability(*self._identity)
        if not self.capability.known:
            self.hint.setText('正在读取模型思考能力；未确认的选项在请求中使用模型默认。')

    def _checked(self, result):
        if (result.sequence != self._sequence or
                (result.base_url, result.model, result.version) != self._identity):
            return
        self._sequence = None
        self.capability = result.thinking
        cache_thinking(*self._identity, self.capability)
        self._fill(self.currentData())

    def _fill(self, setting):
        setting = normalize_think(setting, allow_inherit=self.allow_inherit)
        choices = []
        if self.allow_inherit:
            choices.append(ThinkSetting(ThinkMode.INHERIT))
        choices.append(ThinkSetting())
        if self.capability.known:
            for value in self.capability.values:
                choices.append(ThinkSetting(ThinkMode.NAMED, value) if isinstance(value, str) else
                               normalize_think(value))
        unverified = not self.capability.known and setting not in choices
        if unverified:
            # Preserve saved intent during discovery/offline; do not invent new choices.
            choices.append(setting)
        invalid = setting not in choices
        if invalid:
            setting = ThinkSetting()
        self.combo.blockSignals(True)
        self.combo.clear()
        for choice in choices:
            self.combo.addItem(choice.label + ('（待核验）' if unverified and choice == setting else ''), choice)
        self.combo.setCurrentIndex(choices.index(setting))
        self.combo.blockSignals(False)
        if invalid:
            self.hint.setText('原思考选项不在此模型的声明中，已改为模型默认。')
        else:
            self._selection_changed()

    def _selection_changed(self):
        selected = self.currentData()
        if selected is None:
            return
        if selected.mode == ThinkMode.INHERIT:
            self.hint.setText('继承全局思考设置；发送前按最终模型的能力校验。')
        elif not self.capability.known:
            self.hint.setText(self.capability.error or '思考能力尚未确认；请求使用模型默认，保留已存设置。')
        else:
            default = self.capability.default
            label = ('未声明' if default is None else '开启' if default is True else
                     '关闭' if default is False else default)
            model = self._identity[1] if self._identity else '当前模型'
            self.hint.setText(f'{model} · 模型默认：{label}。选项来自模型声明。')

    def stop(self):
        self._refresh_timer.stop()
        self._sequence = None
        self._checks.cancel_all()
