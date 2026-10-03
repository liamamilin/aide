"""
设置面板
"""
import json
import os
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget

from ai_desktop.ui.fluent import BodyLabel as QLabel
from ai_desktop.ui.fluent import CheckBox as QCheckBox
from ai_desktop.ui.fluent import ComboBox as QComboBox
from ai_desktop.ui.fluent import DoubleSpinBox as QDoubleSpinBox
from ai_desktop.ui.fluent import FluentDialog as QDialog
from ai_desktop.ui.fluent import LineEdit as QLineEdit
from ai_desktop.ui.fluent import MessageBox as QMessageBox
from ai_desktop.ui.fluent import (
    PrimaryPushButton,
    dialog_title,
)
from ai_desktop.ui.fluent import PushButton as QPushButton
from ai_desktop.ui.fluent import ScrollArea as QScrollArea
from ai_desktop.ui.fluent import SpinBox as QSpinBox
from ai_desktop.ui.frameless_mixin import FramelessDragMixin
from ai_desktop.utils.paths import resource_path


def _discover_pets() -> list[tuple[str, str, str]]:
    """Return list of (display_label, source, name) for all available pets."""
    pets: list[tuple[str, str, str]] = []

    built_in_dir = resource_path("ai_desktop", "pets")
    if os.path.isdir(built_in_dir):
        for entry in sorted(os.listdir(built_in_dir)):
            manifest = os.path.join(built_in_dir, entry, "pet.json")
            if os.path.isfile(manifest):
                label = _pet_display_name(manifest, entry)
                pets.append((f"{label} (内置)", "built-in", entry))

    petdex_dir = Path.home() / ".petdex" / "pets"
    if petdex_dir.is_dir():
        for entry in sorted(petdex_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = entry / "pet.json"
            if manifest.is_file():
                label = _pet_display_name(str(manifest), entry.name)
                pets.append((f"{label} (Petdex)", "petdex", entry.name))

    return pets


def _pet_display_name(manifest_path: str, fallback: str) -> str:
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("displayName") or data.get("name") or fallback
    except (OSError, json.JSONDecodeError, KeyError):
        return fallback


class SettingsDialog(FramelessDragMixin, QDialog):
    settings_applied = pyqtSignal(dict)

    FIELDS = [
        ("base_url",    "Ollama 服务地址",   str,   ""),
        ("think",       "模型思考推理",      bool,  True),
        ("quick_actions", "选中文字后显示快捷动作", bool, True),
        ("desktop_pet", "使用桌面宠物悬浮入口", bool, True),
        ("pet_reduce_motion", "减少宠物动画", bool, False),
        ("pet_size", "桌面宠物尺寸", str, "medium"),
        ("pet_source", "桌面宠物", str, "built-in"),
        ("timeout",     "超时 (秒)",         int,   10),
        ("num_ctx",     "上下文窗口",        int,   2048),
        ("num_predict", "最大输出 token",    int,   256),
        ("temperature", "Temperature",       float, 0.7),
        ("top_p",       "Top P",             float, 0.9),
        ("top_k",       "Top K",             int,   40),
        ("repeat_penalty", "Repeat Penalty", float, 1.1),
        ("max_rounds",  "最大保留轮次",      int,   10),
        ("hotkey",      "快捷键",            str,   ""),
    ]

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self._setup_drag(40)
        self._current = current
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setMinimumSize(380, 440)
        self.resize(400, 460)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        root.addWidget(dialog_title(self, '设置'))

        # ── 表单（可滚动）──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(8)

        self._widgets: dict[str, QWidget] = {}

        # 各字段的数值范围
        _int_ranges: dict[str, tuple[int, int]] = {
            "timeout": (1, 600),
            "num_ctx": (256, 999999),
            "num_predict": (1, 999999),
            "top_k": (0, 200),
            "max_rounds": (1, 100),
        }
        _float_ranges: dict[str, tuple[float, float]] = {
            "temperature": (0.0, 2.0),
            "top_p": (0.0, 1.0),
            "repeat_penalty": (0.0, 2.0),
        }


        for key, label, typ, _ in self.FIELDS:
            if key == "pet_size":
                w = QComboBox()
                w.addItem("小", "small")
                w.addItem("中", "medium")
                w.addItem("大", "large")
                self._widgets[key] = w
            elif key == "pet_name":
                continue
            elif key == "pet_source":
                w = QComboBox()
                for display_label, source, name in _discover_pets():
                    w.addItem(display_label, (source, name))
                self._widgets[key] = w
            elif typ is float:
                w = QDoubleSpinBox()
                lo, hi = _float_ranges.get(key, (0.0, 1.0))
                w.setRange(lo, hi)
                w.setSingleStep(0.05)
                w.setDecimals(2)
                self._widgets[key] = w
            elif typ is int:
                w = QSpinBox()
                lo, hi = _int_ranges.get(key, (1, 999999))
                w.setRange(lo, hi)
                self._widgets[key] = w
            elif typ is bool:
                w = QCheckBox()
                self._widgets[key] = w
            else:
                w = QLineEdit()
                self._widgets[key] = w
            lbl = QLabel(label)
            form.addRow(lbl, w)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── 按钮 ──
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = PrimaryPushButton("保存")
        save.clicked.connect(self._on_save)
        bb.addWidget(save)

        root.addLayout(bb)

    def _load(self) -> None:
        for key, label, typ, default in self.FIELDS:
            if key == "pet_name":
                continue
            val = self._current.get(key, default)
            w = self._widgets[key]
            if key == "pet_size":
                index = w.findData(str(val))
                w.setCurrentIndex(index if index >= 0 else w.findData(default))
            elif key == "pet_source":
                source = str(val)
                name = str(self._current.get("pet_name", "owl-v2"))
                target = (source, name)
                index = next(
                    (i for i in range(w.count()) if w.itemData(i) == target),
                    0,
                )
                w.setCurrentIndex(index)
            elif typ is float:
                w.setValue(float(val) if val else float(default))
            elif typ is int:
                w.setValue(int(val) if val else default)
            elif typ is bool:
                w.setChecked(bool(val) if val is not None else bool(default))
            else:
                w.setText(str(val))

    def _on_save(self) -> None:
        data = {}
        for key, label, typ, default in self.FIELDS:
            if key == "pet_name":
                continue
            w = self._widgets[key]
            if key == "pet_size":
                data[key] = w.currentData()
            elif key == "pet_source":
                source, name = w.currentData()
                data["pet_source"] = source
                data["pet_name"] = name
            elif typ is float:
                data[key] = w.value()
            elif typ is int:
                data[key] = w.value()
            elif typ is bool:
                data[key] = w.isChecked()
            else:
                t = w.text().strip()
                data[key] = t if t else str(default)

        # ── 校验 ──
        url = data.get("base_url", "")
        if url and not url.startswith(("http://", "https://")):
            self._widgets["base_url"].setFocus()
            QMessageBox.warning(self, "输入错误", "Ollama 服务地址需要以 http:// 或 https:// 开头")
            return

        hotkey = data.get("hotkey", "")
        if hotkey and ("+" not in hotkey or not hotkey.startswith("<")):
            self._widgets["hotkey"].setFocus()
            QMessageBox.warning(self, "输入错误", "快捷键格式无效，例如: <cmd>+<ctrl>+l")
            return

        self.settings_applied.emit(data)
        self.accept()

    # ── 拖拽 / Esc ──（由 FramelessDragMixin 处理）──
    # mousePressEvent / mouseMoveEvent / mouseReleaseEvent / keyPressEvent
    # 已由 mixin 统一管理，此处不再重复
