"""FloatButton tests — context menu signals and auto-hide toggle."""
from unittest.mock import patch

import pytest


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
        btn._animation_timer.stop()
        return btn


def _get_context_menu(button):
    """Build the context menu the same way contextMenuEvent does, but without exec_.

    This mirrors the FloatButton.contextMenuEvent logic to create a QMenu
    with all the same actions and signal connections, so we can test them.
    """
    return button._create_context_menu()


def test_native_spaces_pin_is_skipped_outside_cocoa(qapp):
    from ai_desktop.ui.float_button import pin_to_all_spaces

    with patch("ai_desktop.ui.float_button.sys.platform", "darwin"), \
            patch("ai_desktop.ui.float_button.ctypes.util.find_library") as find_library:
        pin_to_all_spaces(object())
    find_library.assert_not_called()


# ── L1: Signal Tests ───────────────────────────────────

class TestFloatButtonSignals:
    """Verify context menu signals are emitted correctly."""

    def test_context_menu_has_expected_actions(self, qtbot, button):
        """Context menu should contain all expected actions."""
        menu = _get_context_menu(button)
        assert menu is not None
        action_texts = [a.text() for a in menu.actions()]
        assert "设置…" in action_texts
        assert "桌面宠物形态" in action_texts
        assert "隐藏桌面宠物" in action_texts
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
            if action.text() in {"隐藏桌面宠物", "隐藏悬浮球"}:
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

    def test_pet_mode_toggled_signal(self, qtbot, button):
        menu = _get_context_menu(button)
        action = next(item for item in menu.actions() if item.text() == "桌面宠物形态")
        with qtbot.waitSignal(button.pet_mode_toggled, timeout=1000) as spy:
            action.toggle()
        assert spy.args == [False]

    def test_pet_and_compact_modes_keep_expected_sizes(self, button):
        assert button._pet_enabled
        assert not button._pet_content.isNull()
        assert button.size().width() > 44
        assert button.size().height() > 44
        button.set_pet_enabled(False)
        assert button.size().width() == 44
        assert button.size().height() == 44
        button.set_responding(True)
        assert button._animation_timer.isActive()
        button.set_responding(False)
        assert not button._animation_timer.isActive()
        button.set_pet_enabled(True)
        assert button._pet_enabled

    def test_working_state_has_priority_over_capture_state(self, button):
        button.set_listening(True)
        assert button._effective_state() == "listening"
        button.set_responding(True)
        assert button._effective_state() == "working"
        button.set_responding(False)
        assert button._effective_state() == "listening"
        button.set_listening(False)
        assert button._effective_state() == "idle"

    def test_result_state_returns_to_idle(self, qtbot, button):
        button.show_result(True)
        assert button._effective_state() == "success"
        button._result_timer.start(1)
        qtbot.waitUntil(lambda: button._effective_state() == "idle", timeout=100)

        button.show_result(False)
        assert button._effective_state() == "error"
