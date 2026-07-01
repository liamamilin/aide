"""FloatButton tests — context menu signals and auto-hide toggle."""
from unittest.mock import patch

import pytest
from PyQt5.QtWidgets import QMenu


@pytest.fixture()
def button(qtbot):
    """Create a FloatButton with mocked screen tracking timer and pin_to_all_spaces."""
    with patch("ai_desktop.ui.float_button.pin_to_all_spaces"):
        from ai_desktop.ui.float_button import FloatButton
        btn = FloatButton()
        qtbot.addWidget(btn)
        btn.show()
        # Stop the screen tracking timer to avoid side effects
        if hasattr(btn, '_track_timer'):
            btn._track_timer.stop()
        return btn


def _get_context_menu(button):
    """Build the context menu the same way contextMenuEvent does, but without exec_.

    This mirrors the FloatButton.contextMenuEvent logic to create a QMenu
    with all the same actions and signal connections, so we can test them.
    """
    from ai_desktop.ui import styles
    menu = QMenu(button)
    menu.setStyleSheet(styles.MENU)
    auto_hide_action = menu.addAction("自动收起对话框")
    auto_hide_action.setCheckable(True)
    auto_hide_action.setChecked(button._auto_hide)
    auto_hide_action.toggled.connect(button.auto_hide_toggled.emit)
    menu.addSeparator()
    settings_action = menu.addAction("设置…")
    settings_action.triggered.connect(button.settings_requested.emit)
    hide_action = menu.addAction("隐藏悬浮球")
    hide_action.triggered.connect(button.hide_requested.emit)
    about_action = menu.addAction("关于 AI 桌面助手")
    about_action.triggered.connect(button.about_requested.emit)
    exit_action = menu.addAction("退出")
    exit_action.triggered.connect(button.exit_requested.emit)
    return menu


# ── L1: Signal Tests ───────────────────────────────────

class TestFloatButtonSignals:
    """Verify context menu signals are emitted correctly."""

    def test_context_menu_has_expected_actions(self, qtbot, button):
        """Context menu should contain all expected actions."""
        menu = _get_context_menu(button)
        assert menu is not None
        action_texts = [a.text() for a in menu.actions()]
        assert "设置…" in action_texts
        assert "隐藏悬浮球" in action_texts
        assert "退出" in action_texts

    def test_settings_requested_signal(self, qtbot, button):
        """Triggering settings action → settings_requested signal."""
        menu = _get_context_menu(button)
        settings_action = None
        for action in menu.actions():
            if action.text() == "设置…":
                settings_action = action
                break
        assert settings_action is not None
        with qtbot.waitSignal(button.settings_requested, timeout=1000):
            settings_action.trigger()

    def test_hide_requested_signal(self, qtbot, button):
        """Triggering hide action → hide_requested signal."""
        menu = _get_context_menu(button)
        hide_action = None
        for action in menu.actions():
            if action.text() == "隐藏悬浮球":
                hide_action = action
                break
        assert hide_action is not None
        with qtbot.waitSignal(button.hide_requested, timeout=1000):
            hide_action.trigger()

    def test_exit_requested_signal(self, qtbot, button):
        """Triggering exit action → exit_requested signal."""
        menu = _get_context_menu(button)
        exit_action = None
        for action in menu.actions():
            if action.text() == "退出":
                exit_action = action
                break
        assert exit_action is not None
        with qtbot.waitSignal(button.exit_requested, timeout=1000):
            exit_action.trigger()

    def test_about_requested_signal(self, qtbot, button):
        """Triggering about action → about_requested signal."""
        menu = _get_context_menu(button)
        about_action = None
        for action in menu.actions():
            if action.text() == "关于 AI 桌面助手":
                about_action = action
                break
        assert about_action is not None
        with qtbot.waitSignal(button.about_requested, timeout=1000):
            about_action.trigger()


# ── L2: State Tests ────────────────────────────────────

class TestFloatButtonState:
    """Verify auto-hide state toggling."""

    def test_auto_hide_toggle_on(self, qtbot, button):
        """set_auto_hide_state(True) → _auto_hide is True."""
        button.set_auto_hide_state(True)
        assert button._auto_hide is True

    def test_auto_hide_toggle_off(self, qtbot, button):
        """set_auto_hide_state(False) → _auto_hide is False."""
        button.set_auto_hide_state(True)
        button.set_auto_hide_state(False)
        assert button._auto_hide is False

    def test_auto_hide_toggled_signal(self, qtbot, button):
        """Toggling auto-hide action → auto_hide_toggled signal with bool."""
        menu = _get_context_menu(button)
        auto_hide_action = None
        for action in menu.actions():
            if action.text() == "自动收起对话框":
                auto_hide_action = action
                break
        assert auto_hide_action is not None
        assert auto_hide_action.isCheckable()
        with qtbot.waitSignal(button.auto_hide_toggled, timeout=1000) as spy:
            auto_hide_action.toggle()
        # Signal should carry a bool
        assert len(spy.args) == 1
        assert isinstance(spy.args[0], bool)
