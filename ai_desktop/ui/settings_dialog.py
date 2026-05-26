"""
设置面板
"""
from PyQt5.QtCore import Qt, QPoint, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    settings_applied = pyqtSignal(dict)  # 保存后发射所有配置项

    FIELDS = [
        ("base_url",   "Ollama 服务地址",   str, ""),
        ("timeout",    "超时 (秒)",         int, 10),
        ("num_ctx",    "上下文窗口",        int, 2048),
        ("num_predict","最大输出 token",    int, 256),
        ("hotkey",     "快捷键",            str, ""),
    ]

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self._current = current
        self._drag_pos: QPoint | None = None
        self._values: dict = {}
        self._setup_window()
        self._setup_ui()
        self._load()

    def _setup_window(self):
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setMinimumSize(380, 280)
        self.resize(400, 300)
        self.setStyleSheet("QDialog { background: #ffffff; border-radius: 10px; }")

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 标题栏 ──
        title = QWidget()
        title.setFixedHeight(40)
        title.setStyleSheet(
            "background: #f6f6f6; border-top-left-radius: 10px; border-top-right-radius: 10px;"
        )
        tl = QHBoxLayout(title)
        tl.setContentsMargins(12, 0, 8, 0)

        title_lbl = QLabel("设置")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; background: none; color: #1a1a1a;")
        tl.addWidget(title_lbl)
        tl.addStretch()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 16px; color: #999; }"
            "QPushButton:hover { color: #333; }"
        )
        close_btn.clicked.connect(self.reject)
        tl.addWidget(close_btn)

        root.addWidget(title)

        # ── 表单 ──
        form = QFormLayout()
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(8)

        self._widgets: dict[str, QWidget] = {}

        for key, label, typ, _ in self.FIELDS:
            if typ is int:
                w = QSpinBox()
                w.setRange(1, 999999)
                w.setStyleSheet("QSpinBox { border: 1px solid #ddd; border-radius: 4px; padding: 3px 6px; }")
                self._widgets[key] = w
            else:
                w = QLineEdit()
                w.setStyleSheet("QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 4px 8px; }"
                                "QLineEdit:focus { border-color: #007AFF; }")
                self._widgets[key] = w
            form.addRow(QLabel(label), w)

        root.addLayout(form)

        # ── 按钮 ──
        bb = QHBoxLayout()
        bb.setContentsMargins(16, 8, 16, 12)
        bb.addStretch()

        cancel = QPushButton("取消")
        cancel.setStyleSheet(
            "QPushButton { background: #e8e8e8; border: none; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background: #d0d0d0; }"
        )
        cancel.clicked.connect(self.reject)
        bb.addWidget(cancel)

        save = QPushButton("保存")
        save.setStyleSheet(
            "QPushButton { background: #007AFF; color: white; border: none; border-radius: 4px;"
            "padding: 6px 16px; }"
            "QPushButton:hover { background: #0066d6; }"
        )
        save.clicked.connect(self._on_save)
        bb.addWidget(save)

        root.addLayout(bb)

    def _load(self) -> None:
        for key, label, typ, default in self.FIELDS:
            val = self._current.get(key, default)
            w = self._widgets[key]
            if typ is int:
                w.setValue(int(val) if val else default)
            else:
                w.setText(str(val))

    def _on_save(self) -> None:
        data = {}
        for key, label, typ, default in self.FIELDS:
            w = self._widgets[key]
            if typ is int:
                data[key] = w.value()
            else:
                t = w.text().strip()
                data[key] = t if t else str(default)
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
