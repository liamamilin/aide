"""FramelessDragMixin tests - verify MRO is correct."""
from PyQt5.QtWidgets import QDialog, QWidget

from ai_desktop.ui.frameless_mixin import FramelessDragMixin


class TestWidget(FramelessDragMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_drag(40)


class TestDialog(FramelessDragMixin, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_drag(40)


def test_mixin_widget_mro():
    """MRO: FramelessDragMixin before QWidget - super().mousePressEvent targets QWidget"""
    w = TestWidget()
    assert isinstance(w, QWidget)
    assert isinstance(w, FramelessDragMixin)
    assert w._title_bar_height == 40


def test_mixin_dialog_mro():
    """MRO: FramelessDragMixin before QDialog - super().mousePressEvent targets QDialog"""
    d = TestDialog()
    assert isinstance(d, QDialog)
    assert isinstance(d, FramelessDragMixin)
    assert d._title_bar_height == 40
