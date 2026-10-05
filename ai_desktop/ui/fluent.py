"""Fluent components and narrow adapters for the application's Qt APIs.

Appearance belongs to PyQt-Fluent-Widgets. Adapters only preserve action/data
contracts and window lifetime; they do not paint or restyle controls.
"""

import os
import sys
from contextlib import redirect_stdout
from io import StringIO

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QAction, QApplication, QWidget
from PyQt5.QtWidgets import QDialog as QtDialog
from PyQt5.QtWidgets import QMessageBox as QtMessageBox
from qframelesswindow import FramelessDialog, FramelessWindow

# Upstream config prints a promotional banner on import. Keep the app's
# version/OCR/speech JSON protocols clean; exceptions and stderr stay visible.
with redirect_stdout(StringIO()):
    from qfluentwidgets import (
        BodyLabel,
        CaptionLabel,
        CheckBox,
        Dialog,
        DotInfoBadge,
        DoubleSpinBox,
        FluentIcon,
        FluentTitleBar,
        InfoBar,
        InfoBarIcon,
        InfoBarPosition,
        InfoLevel,
        LineEdit,
        Pivot,
        PlainTextEdit,
        PrimaryPushButton,
        PushButton,
        RoundMenu,
        ScrollArea,
        SimpleCardWidget,
        SpinBox,
        StrongBodyLabel,
        SubtitleLabel,
        TextEdit,
        Theme,
        TogglePushButton,
        TransparentDropDownToolButton,
        TransparentPushButton,
        TransparentToolButton,
        setTheme,
    )
    from qfluentwidgets import (
        ComboBox as FluentComboBox,
    )



def initialize() -> None:
    """Use the library's Light theme and font, independent of the OS theme."""
    from qfluentwidgets import qconfig

    if qconfig.theme != Theme.LIGHT:
        setTheme(Theme.LIGHT)
    app = QApplication.instance()
    if app is not None:
        from qfluentwidgets import getFont

        app.setFont(getFont())


# Cocoa's native frameless backend requires an NSWindow; headless tests have
# none. The same real Fluent title bar and components are used in both paths.
_headless = os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal"}


class _LightWindow:
    def setWindowFlags(self, flags):
        if sys.platform == "darwin" and not _headless:
            # The library hides the native title bar itself. Removing Qt's
            # native frame destroys its Cocoa window buttons and breaks the
            # upstream frameless backend during showEvent.
            flags = (flags & ~Qt.FramelessWindowHint) | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
            flags |= Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        super().setWindowFlags(flags)

    def _init_fluent(self, dialog=False):
        initialize()
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(240, 244, 249))
        palette.setColor(QPalette.WindowText, QColor("#000000"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        title = FluentTitleBar(self)
        if hasattr(self, "setTitleBar"):
            self.setTitleBar(title)
        else:
            self.titleBar = title
        if dialog:
            title.minBtn.hide()
            title.maxBtn.hide()
            title.setDoubleClickEnabled(False)


class FluentDialog(_LightWindow, QtDialog if _headless else FramelessDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_fluent(dialog=True)


class FluentWindow(_LightWindow, QWidget if _headless else FramelessWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_fluent()


def dialog_title(window, title):
    window.setWindowTitle(title)
    window.titleBar.setTitle(title)
    return window.titleBar


class ComboBox(FluentComboBox):
    """Preserve QComboBox's positional userData convention."""

    def addItem(self, text, userData=None):  # noqa: N803 - Qt compatibility
        super().addItem(text, userData=userData)

    def insertItem(self, index, text, userData=None):  # noqa: N803 - Qt compatibility
        super().insertItem(index, text, userData=userData)


class Menu(RoundMenu):
    """Preserve QMenu's text/callback overloads using Fluent action rows."""

    def __init__(self, title="", parent=None):
        if isinstance(title, QWidget):
            parent, title = title, ""
        super().__init__(title, parent)

    def addAction(self, action, callback=None):
        if isinstance(action, str):
            action = QAction(action, self)
        if callback is not None:
            action.triggered.connect(callback)
        super().addAction(action)
        return action

    def addMenu(self, menu):
        if isinstance(menu, str):
            menu = Menu(menu, self)
        super().addMenu(menu)
        return menu

    def exec_(self, pos, *args, **kwargs):
        # RoundMenu's exec_ animates without QMenu's nested event loop. Callers
        # connect actions before opening and never depend on a returned action.
        return super().exec_(pos, *args, **kwargs)


def editor_menu(editor):
    """Reuse the editor's real cut/copy/paste actions in a Fluent menu."""
    native = editor.createStandardContextMenu()
    menu = Menu(editor)
    menu._source_menu = native
    for action in native.actions():
        if action.isSeparator():
            menu.addSeparator()
        else:
            menu.addAction(action)
    return menu


if _headless:
    from qfluentwidgets.components.dialog_box.dialog import Ui_MessageBox

    class _ConfirmationDialog(QtDialog, Ui_MessageBox):
        def __init__(self, title, content, parent=None):
            super().__init__(parent)
            self._setUpUi(title, content, self)
else:
    _ConfirmationDialog = Dialog


class MessageBox(_ConfirmationDialog):
    """Fluent confirmation dialog retaining QMessageBox result constants."""

    Information = QtMessageBox.Information
    Warning = QtMessageBox.Warning
    Critical = QtMessageBox.Critical
    Ok = QtMessageBox.Ok
    Yes = QtMessageBox.Yes
    No = QtMessageBox.No
    ActionRole = QtMessageBox.ActionRole

    def __init__(self, icon, title, text, buttons=Ok, parent=None):
        initialize()
        super().__init__(title, text, parent)
        self._clicked_button = None
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        if not buttons & self.No:
            self.hideCancelButton()
        self.yesButton.clicked.connect(lambda: self._remember(self.yesButton))
        self.cancelButton.clicked.connect(lambda: self._remember(self.cancelButton))
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

    def _remember(self, button):
        self._clicked_button = button

    def clickedButton(self):
        return self._clicked_button

    def addButton(self, text, role):
        button = PushButton(text, self)
        button.clicked.connect(lambda: (self._remember(button), self.accept()))
        self.buttonLayout.addWidget(button)
        self.setFixedSize(self.sizeHint())
        return button

    @classmethod
    def warning(cls, parent, title, text):
        return cls(cls.Warning, title, text, parent=parent).exec_()

    @classmethod
    def about(cls, parent, title, text):
        return cls(cls.Information, title, text, parent=parent).exec_()

    @classmethod
    def question(cls, parent, title, text, buttons=Yes | No, defaultButton=No):  # noqa: N803
        dialog = cls(cls.Information, title, text, buttons, parent)
        dialog.yesButton.setText("确认")
        if defaultButton == cls.No:
            dialog.cancelButton.setFocus()
        return cls.Yes if dialog.exec_() == QtDialog.Accepted else cls.No


class InputDialog:
    @staticmethod
    def getText(parent, title, label, mode=LineEdit.Normal, text=""):
        dialog = MessageBox(MessageBox.Information, title, label, MessageBox.Yes | MessageBox.No, parent)
        field = LineEdit(dialog)
        field.setEchoMode(mode)
        field.setText(text)
        field.selectAll()
        dialog.textLayout.addWidget(field)
        dialog.setFixedSize(dialog.sizeHint())
        field.setFocus()
        accepted = dialog.exec_() == QtDialog.Accepted
        return field.text(), accepted


def show_notice(parent, icon, title, text):
    """Default Fluent notification, on the visible window or the desktop."""
    severity = {MessageBox.Warning: InfoBarIcon.WARNING, MessageBox.Critical: InfoBarIcon.ERROR}.get(
        icon, InfoBarIcon.INFORMATION
    )
    visible_parent = parent if parent and parent.isVisible() else None
    bar = InfoBar(
        severity,
        title,
        text,
        Qt.Vertical,
        True,
        8000,
        InfoBarPosition.TOP_RIGHT if visible_parent else InfoBarPosition.NONE,
        visible_parent,
    )
    if visible_parent is None:
        bar.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        bar.setAttribute(Qt.WA_ShowWithoutActivating)
        bar.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            bar.move(area.right() - bar.width() - 16, area.top() + 16)
    return bar


__all__ = [
    "BodyLabel",
    "CaptionLabel",
    "CheckBox",
    "ComboBox",
    "Dialog",
    "DotInfoBadge",
    "DoubleSpinBox",
    "FluentComboBox",
    "FluentDialog",
    "FluentIcon",
    "FluentTitleBar",
    "FluentWindow",
    "InfoBar",
    "InfoBarIcon",
    "InfoBarPosition",
    "InfoLevel",
    "InputDialog",
    "LineEdit",
    "Menu",
    "MessageBox",
    "PlainTextEdit",
    "Pivot",
    "PrimaryPushButton",
    "PushButton",
    "RoundMenu",
    "ScrollArea",
    "SimpleCardWidget",
    "SpinBox",
    "StrongBodyLabel",
    "SubtitleLabel",
    "TextEdit",
    "Theme",
    "TogglePushButton",
    "TransparentPushButton",
    "TransparentToolButton",
    "TransparentDropDownToolButton",
    "dialog_title",
    "editor_menu",
    "initialize",
    "setTheme",
    "show_notice",
]
