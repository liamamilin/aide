"""
Frameless 窗口拖拽 Mixin 和 TitleBar 组件

消除 6 个文件中重复的拖拽和 Escape 逻辑。
"""
from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ai_desktop.ui import styles


class TitleBar(QWidget):
    """统一的无边框对话框标题栏"""

    close_clicked = pyqtSignal()

    def __init__(self, title: str, height: int = 44, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setStyleSheet(styles.TITLE_BAR)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)

        label = QLabel(title)
        label.setObjectName("dialogTitle")
        label.setStyleSheet(styles.LABEL_BOLD)
        layout.addWidget(label)
        layout.addStretch()

        close_btn = QPushButton("×")
        close_btn.setObjectName("dialogCloseButton")
        close_btn.setToolTip("关闭")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(styles.CLOSE_BUTTON)
        close_btn.clicked.connect(self.close_clicked.emit)
        layout.addWidget(close_btn)


class FramelessDragMixin:
    """无边框窗口拖拽 + Escape 关闭混入类

    用法：
        class MyDialog(QDialog, FramelessDragMixin):
            def __init__(self):
                super().__init__()
                self._setup_drag(title_height=40)
                # 调用 _setup_drag 即可获得拖拽 + Escape 关闭能力
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_pos: QPoint | None = None
        self._title_bar_height = 40

    def _setup_drag(self, title_bar_height: int = 40):
        self._title_bar_height = title_bar_height

    def _in_drag_area(self, pos: QPoint) -> bool:
        return pos.y() <= self._title_bar_height

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._in_drag_area(event.pos()):
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)
