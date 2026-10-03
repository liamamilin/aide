"""Fixed Fluent Light, rich-text preservation and owned-window regressions."""

from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel
from qfluentwidgets import FluentTitleBar, LineEdit, Theme, qconfig, setTheme

from ai_desktop.config import Agent
from ai_desktop.ui import styles, theme
from ai_desktop.ui.agent_editor import AgentDef, AgentEditor
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.fluent import Menu, PrimaryPushButton
from ai_desktop.ui.history_dialog import HistoryDialog
from ai_desktop.ui.menubar_icon import MenuBarIcon
from ai_desktop.ui.settings_dialog import SettingsDialog


@pytest.fixture()
def dialog(qtbot):
    agent = Agent("general_assistant", "通用助手", "🤖", "Help.")
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        window = ChatDialog([agent], agent, ["model"], "model")
    qtbot.addWidget(window)
    window.show()
    return window


def test_refresh_restores_library_light_without_overriding_components(qapp, qtbot):
    button, field = PrimaryPushButton("保存"), LineEdit()
    qtbot.addWidget(button)
    qtbot.addWidget(field)
    before = button.styleSheet(), field.styleSheet()
    setTheme(Theme.DARK)
    styles.refresh_all(qapp)
    assert qconfig.theme == Theme.LIGHT
    assert (button.styleSheet().strip(), field.styleSheet().strip()) == tuple(x.strip() for x in before)


def test_open_dialogs_keep_light_frames_and_window_behavior(qapp, qtbot, tmp_db, monkeypatch):
    windows = [SettingsDialog({}), HistoryDialog(), AgentEditor([AgentDef("a", "助手", "🤖", "Help.", True)], [])]
    for window in windows:
        qtbot.addWidget(window)
        window.show()
        assert isinstance(window.titleBar, FluentTitleBar)
        assert window.windowFlags() & Qt.WindowStaysOnTopHint
        assert not window.testAttribute(Qt.WA_TranslucentBackground)
    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    styles.refresh_all(qapp)
    assert qconfig.theme == Theme.LIGHT
    assert theme.current() == theme.LIGHT
    assert isinstance(windows[0]._widgets["base_url"], LineEdit)


def test_chat_refresh_preserves_messages_and_markdown(qapp, dialog, monkeypatch):
    dialog.add_user_message("问题")
    dialog.add_assistant_message("# 标题\n`代码`")
    count = dialog._msg_layout.count()
    label = next(x for x in dialog._msg_container.findChildren(QLabel) if hasattr(x, "_markdown_source"))
    original = label.text()
    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    styles.refresh_all(qapp)
    dialog.refresh_theme()
    assert dialog._msg_layout.count() == count
    assert label.text() == original
    assert "标题" in label.text() and "代码" in label.text()


def test_finalized_thinking_survives_refresh(qapp, dialog):
    dialog.begin_assistant_stream()
    dialog.append_thinking_chunk("思考文本")
    dialog.append_stream_chunk("答案")
    dialog.finalize_assistant_stream("答案", ok=True)
    dialog.refresh_theme()
    label = next(
        x for x in dialog._msg_container.findChildren(QLabel) if getattr(x, "_thinking_source", "") == "思考文本"
    )
    assert "思考文本" in label.text() and "答案" in label.text()


def test_tray_preserves_default_fluent_menu_on_refresh(qapp):
    agent = Agent("general_assistant", "通用助手", "🤖", "Help.")
    tray = MenuBarIcon([agent], agent, parent=qapp)
    menu = tray.contextMenu()
    assert isinstance(menu, Menu)
    before = menu.styleSheet()
    tray.refresh_theme()
    assert menu.styleSheet() == before
    tray.deleteLater()


def test_auto_hide_keeps_owned_child_flow_visible(dialog, monkeypatch):
    child = QDialog(dialog)
    unrelated = QDialog()
    dialog.set_auto_hide(True)
    monkeypatch.setattr(dialog, "isActiveWindow", lambda: False)

    assert dialog._owns_window(child)
    assert not dialog._owns_window(unrelated)

    monkeypatch.setattr(dialog, "_has_active_owned_window", lambda: True)
    dialog._apply_auto_hide()
    assert dialog.isVisible()

    monkeypatch.setattr(dialog, "_has_active_owned_window", lambda: False)
    dialog._apply_auto_hide()
    assert not dialog.isVisible()
    unrelated.deleteLater()


def test_native_file_picker_suspends_auto_hide_and_restores_dialog(
    dialog,
    monkeypatch,
):
    dialog.set_auto_hide(True)
    monkeypatch.setattr(dialog, "isActiveWindow", lambda: False)

    def fake_picker(*_args, **_kwargs):
        assert dialog._auto_hide_suspended
        dialog._apply_auto_hide()
        assert dialog.isVisible()
        dialog.hide()
        return [], ""

    monkeypatch.setattr(
        "ai_desktop.ui.chat_dialog.QFileDialog.getOpenFileNames",
        fake_picker,
    )

    dialog._pick_image_files()

    assert dialog.isVisible()
    assert not dialog._auto_hide_suspended
