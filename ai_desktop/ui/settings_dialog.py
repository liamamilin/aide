"""
设置面板
"""
from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
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

from ai_desktop.ui import styles


class SettingsDialog(QDialog):
    settings_applied = pyqtSignal(dict)  # 保存后发射所有配置项

    FIELDS = [
        # (key, label, type, default)
        ("base_url",    "Ollama 服务地址",   str,   ""),
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
        self._current = current
        self._drag_pos: QPoint | None = None
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(380, 440)
        self.resize(400, 460)
        self.setStyleSheet(styles.DIALOG_BASE)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(styles.TITLE_BAR)
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)

        title_lbl = QLabel("设置")
        title_lbl.setStyleSheet(styles.LABEL_BOLD)
        tl.addWidget(title_lbl)
        tl.addStretch()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(styles.CLOSE_BUTTON)
        close_btn.clicked.connect(self.reject)
        tl.addWidget(close_btn)

        root.addWidget(title)

        # ── 表单（可滚动）──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(styles.SETTINGS_SCROLL)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
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

        _widget_style = styles.FORM_WIDGET

        for key, label, typ, _ in self.FIELDS:
            if typ is float:
                w = QDoubleSpinBox()
                lo, hi = _float_ranges.get(key, (0.0, 1.0))
                w.setRange(lo, hi)
                w.setSingleStep(0.05)
                w.setDecimals(2)
                w.setStyleSheet(f"QDoubleSpinBox {{ {_widget_style} }}")
                self._widgets[key] = w
            elif typ is int:
                w = QSpinBox()
                lo, hi = _int_ranges.get(key, (1, 999999))
                w.setRange(lo, hi)
                w.setStyleSheet(f"QSpinBox {{ {_widget_style} }}")
                self._widgets[key] = w
            else:
                w = QLineEdit()
                w.setStyleSheet(
                    f"QLineEdit {{ {_widget_style} }}"
                    "QLineEdit:focus { border-color: #007AFF; }"
                )
                self._widgets[key] = w
            lbl = QLabel(label)
            lbl.setStyleSheet(styles.LABEL)
            form.addRow(lbl, w)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── 按钮 ──
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.setStyleSheet(styles.CANCEL_BUTTON)
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = QPushButton("保存")
        save.setStyleSheet(styles.SAVE_BUTTON)
        save.clicked.connect(self._on_save)
        bb.addWidget(save)

        root.addLayout(bb)

    def _load(self) -> None:
        for key, label, typ, default in self.FIELDS:
            val = self._current.get(key, default)
            w = self._widgets[key]
            if typ is float:
                w.setValue(float(val) if val else float(default))
            elif typ is int:
                w.setValue(int(val) if val else default)
            else:
                w.setText(str(val))

    def _on_save(self) -> None:
        data = {}
        for key, label, typ, default in self.FIELDS:
            w = self._widgets[key]
            if typ is float:
                data[key] = w.value()
            elif typ is int:
                data[key] = w.value()
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

    # ── 拖拽 / Esc ─────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.pos().y() <= 40:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)
