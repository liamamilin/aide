"""A stable model-step card; its last step becomes the final answer in place."""
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout

from ai_desktop.services.audit_store import RETENTION_SECONDS
from ai_desktop.ui.fluent import (
    BodyLabel,
    CaptionLabel,
    PlainTextEdit,
    SimpleCardWidget,
    StrongBodyLabel,
    TransparentPushButton,
)


class TaskStepCard(SimpleCardWidget):
    def __init__(self, event, parent=None, *, label_factory=BodyLabel):
        super().__init__(parent)
        self.identity = (event.run_id, event.step_id, event.request_id)
        self.terminal = False
        self.final_answer = False
        self.payload_expires_at = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self.header = StrongBodyLabel('模型处理中…')
        layout.addWidget(self.header)
        self.body = label_factory('')
        self.body.setObjectName('message_bubble')
        self.body.setTextFormat(Qt.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        layout.addWidget(self.body)
        self.thinking_toggle = TransparentPushButton('查看思考')
        self.thinking_toggle.hide()
        self.thinking = PlainTextEdit()
        self.thinking.setReadOnly(True)
        self.thinking.setFixedHeight(140)
        self.thinking.hide()
        self.thinking_toggle.clicked.connect(self._toggle_thinking)
        layout.addWidget(self.thinking_toggle, alignment=Qt.AlignLeft)
        layout.addWidget(self.thinking)
        self.status = CaptionLabel('')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.copy = TransparentPushButton('复制')
        self.copy.setObjectName('copy_btn_assistant')
        self.regenerate = TransparentPushButton('重新执行')
        self.regenerate.setObjectName('regen_btn_assistant')
        self.version = TransparentPushButton('')
        self.version.setObjectName('version_btn_assistant')
        for button in (self.copy, self.regenerate, self.version):
            button.hide()
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

    def matches(self, event):
        return self.identity == (event.run_id, event.step_id, event.request_id)

    def _toggle_thinking(self):
        self.thinking.setVisible(not self.thinking.isVisible())
        self.thinking_toggle.setText('收起思考' if self.thinking.isVisible() else '查看思考')

    def set_thinking(self, text):
        self.thinking.setPlainText(text)
        self.thinking_toggle.setVisible(bool(text))

    def finish(self, status, *, final=False):
        self.terminal = True
        self.final_answer = final
        self.header.setText('回答' if final else '模型步骤')
        self.status.setText(status)
        self.payload_expires_at = time.time() + RETENTION_SECONDS

    def purge(self, now):
        if self.terminal and self.payload_expires_at and self.payload_expires_at <= now:
            if not self.final_answer:
                self.body.setText('执行详情已过期')
            self.thinking.clear()
            self.thinking.hide()
            self.thinking_toggle.hide()
