"""Editable preview for text extracted from one pending image."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ai_desktop.ui import styles


class OCRPreviewDialog(QDialog):
    text_accepted = pyqtSignal(str)
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image_path = ""
        self._loading = False
        self.setWindowTitle("提取文字")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.setMinimumSize(460, 320)
        self.resize(520, 420)
        self.setStyleSheet(styles.CHAT_DIALOG_ROOT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self._source = QLabel()
        self._source.setStyleSheet(styles.LABEL_SECONDARY)
        layout.addWidget(self._source)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("识别结果将在这里显示，可直接修订。")
        self._editor.setStyleSheet(styles.INPUT_AREA)
        layout.addWidget(self._editor, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._copy_button = QPushButton("复制文字")
        self._copy_button.setStyleSheet(styles.SECONDARY_BUTTON)
        self._copy_button.clicked.connect(self._copy_text)
        buttons.addWidget(self._copy_button)
        self._use_button = QPushButton("插入输入框")
        self._use_button.setStyleSheet(styles.BUTTON_PRIMARY)
        self._use_button.clicked.connect(self._accept_text)
        buttons.addWidget(self._use_button)
        close_button = QPushButton("关闭")
        close_button.setStyleSheet(styles.SECONDARY_BUTTON)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self._set_actions_enabled(False)

    @property
    def image_path(self) -> str:
        return self._image_path

    @property
    def is_loading(self) -> bool:
        return self._loading

    def begin(self, image_path: str) -> None:
        self._image_path = image_path
        self._loading = True
        self._source.setText(f"图片：{Path(image_path).name}")
        self._status.setText("正在使用 Apple Vision 在本机提取文字…")
        self._editor.clear()
        self._editor.setEnabled(False)
        self._set_actions_enabled(False)
        self.show()
        self.activateWindow()
        self.raise_()

    def show_result(
        self,
        text: str,
        *,
        block_count: int,
        elapsed_ms: float,
        languages: tuple[str, ...],
        low_confidence: bool,
    ) -> None:
        self._loading = False
        language_text = ", ".join(languages) if languages else "自动"
        status = (
            f"已提取 {block_count} 个文本块 · {elapsed_ms:.1f} ms · "
            f"语言 {language_text}"
        )
        if low_confidence:
            status += "\n部分内容置信度较低，请对照原图检查。"
        self._status.setText(status)
        self._editor.setEnabled(True)
        self._editor.setPlainText(text)
        self._set_actions_enabled(bool(text.strip()))
        self._editor.setFocus()

    def show_empty(self, elapsed_ms: float) -> None:
        self._loading = False
        self._status.setText(
            f"未识别到文字（{elapsed_ms:.1f} ms）。可关闭后换一张更清晰的图片重试。"
        )
        self._editor.clear()
        self._editor.setEnabled(False)
        self._set_actions_enabled(False)

    def show_error(self, error: str) -> None:
        self._loading = False
        self._status.setText(f"提取失败：{error or '请稍后重试。'}")
        self._editor.clear()
        self._editor.setEnabled(False)
        self._set_actions_enabled(False)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._copy_button.setEnabled(enabled)
        self._use_button.setEnabled(enabled)

    def _copy_text(self) -> None:
        text = self._editor.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self._status.setText("已复制当前修订文字。")

    def _accept_text(self) -> None:
        text = self._editor.toPlainText()
        if not text.strip():
            return
        self.text_accepted.emit(text)
        self.close()

    def closeEvent(self, event) -> None:
        if self._loading:
            self.cancel_requested.emit()
        self._loading = False
        super().closeEvent(event)
