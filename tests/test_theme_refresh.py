"""Runtime theme refresh and owned-window focus regressions."""

from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel, QLineEdit, QPushButton

from ai_desktop.config import Agent
from ai_desktop.ui import styles, theme
from ai_desktop.ui.agent_editor import AgentDef, AgentEditor
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.history_dialog import HistoryDialog
from ai_desktop.ui.menubar_icon import MenuBarIcon
from ai_desktop.ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def reset_style_cache():
    styles.invalidate()
    yield
    styles.invalidate()


@pytest.fixture()
def dialog(qtbot):
    agent = Agent(
        id="general_assistant",
        name="通用助手",
        icon="🤖",
        system_prompt="Help.",
    )
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        window = ChatDialog([agent], agent, ["model"], "model")
    qtbot.addWidget(window)
    window.show()
    return window


def test_refresh_all_updates_existing_and_embedded_styles(qapp, qtbot, monkeypatch):
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    styles.invalidate()
    button = QPushButton()
    field = QLineEdit()
    qtbot.addWidget(button)
    qtbot.addWidget(field)
    button.setStyleSheet(styles.BUTTON_PRIMARY)
    field.setStyleSheet(f"QLineEdit {{ {styles.FORM_WIDGET} }}")

    assert theme.LIGHT.accent in button.styleSheet()
    assert theme.LIGHT.window in field.styleSheet()

    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    refreshed = styles.refresh_all(qapp)

    assert refreshed >= 2
    assert theme.DARK.accent in button.styleSheet()
    assert theme.LIGHT.accent not in button.styleSheet()
    assert theme.DARK.window in field.styleSheet()
    assert theme.LIGHT.window not in field.styleSheet()

    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    styles.refresh_all(qapp)

    assert theme.LIGHT.accent in button.styleSheet()
    assert theme.DARK.accent not in button.styleSheet()
    assert theme.LIGHT.window in field.styleSheet()


def test_refresh_all_updates_every_open_dialog_type(
    qapp, qtbot, tmp_db, monkeypatch,
):
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    styles.invalidate()
    settings = SettingsDialog({})
    history = HistoryDialog()
    editor = AgentEditor(
        [AgentDef("builtin", "内置", "🤖", "Help.", builtin=True)],
        [],
    )
    for window in (settings, history, editor):
        qtbot.addWidget(window)
        window.show()
        assert theme.LIGHT.window in window.styleSheet()
        assert bool(window.windowFlags() & Qt.WindowStaysOnTopHint)

    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    refreshed = styles.refresh_all(qapp)

    assert refreshed > 0
    for window in (settings, history, editor):
        assert theme.DARK.window in window.styleSheet()
        assert theme.LIGHT.window not in window.styleSheet()
    assert theme.DARK.window in settings._widgets["base_url"].styleSheet()
    assert theme.DARK.accent in settings._widgets["base_url"].styleSheet()
    empty_states = [
        label for label in history.findChildren(QLabel)
        if label.styleSheet() == styles.EMPTY_STATE
    ]
    assert empty_states
    assert theme.DARK.text_secondary in empty_states[0].styleSheet()


def test_chat_theme_refresh_preserves_messages_and_rerenders_markdown(
    qapp, dialog, monkeypatch,
):
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    styles.invalidate()
    dialog.setStyleSheet(styles.CHAT_DIALOG_ROOT)
    dialog.add_user_message("问题")
    dialog.add_assistant_message("# 标题\n`代码`")
    before_count = dialog._msg_layout.count()
    label = next(
        item
        for item in dialog._msg_container.findChildren(object)
        if getattr(item, "_markdown_source", None) is not None
    )
    assert theme.LIGHT.accent in label.text()

    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    styles.refresh_all(qapp)
    dialog.refresh_theme()

    assert dialog._msg_layout.count() == before_count
    assert "标题" in label.text()
    assert "代码" in label.text()
    assert theme.DARK.accent in label.text()
    assert theme.DARK.surface in label.text()
    assert theme.DARK.window in dialog.styleSheet()


def test_finalized_thinking_is_rerendered_with_new_theme(qapp, dialog, monkeypatch):
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    styles.invalidate()
    dialog.begin_assistant_stream()
    dialog.append_thinking_chunk("思考文本")
    dialog.append_stream_chunk("答案")
    dialog.finalize_assistant_stream("答案", ok=True)
    label = next(
        item
        for item in dialog._msg_container.findChildren(object)
        if getattr(item, "_thinking_source", "") == "思考文本"
    )

    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    styles.refresh_all(qapp)
    dialog.refresh_theme()

    assert "思考文本" in label.text()
    assert theme.DARK.text_secondary in label.text()
    assert theme.DARK.text in label.text()


def test_tray_refreshes_persistent_menu(qapp, monkeypatch):
    agent = Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="Help.")
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    tray = MenuBarIcon([agent], agent, parent=qapp)
    assert theme.LIGHT.window in tray.contextMenu().styleSheet()

    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    tray.refresh_theme()

    assert theme.DARK.window in tray.contextMenu().styleSheet()
    assert theme.LIGHT.window not in tray.contextMenu().styleSheet()
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
    dialog, monkeypatch,
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
