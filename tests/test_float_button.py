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
        button.set_quick_actions(
            [("translate", "翻译"), ("explain", "解释"), ("rewrite", "改写")]
        )
        menu = _get_context_menu(button)
        assert menu is not None
        action_texts = [a.text() for a in menu.actions()]
        assert action_texts[:2] == ["截图到对话…", ""]
        assert action_texts[2:6] == ["最近快捷动作", "⚡  翻译", "⚡  解释", "⚡  改写"]
        assert "设置…" in action_texts
        assert "桌面宠物形态" in action_texts
        assert "隐藏桌面宠物" in action_texts
        assert "退出" in action_texts

    def test_screenshot_action_emits_signal(self, qtbot, button):
        menu = _get_context_menu(button)
        action = next(item for item in menu.actions() if item.data() == "screenshot")
        with qtbot.waitSignal(button.screenshot_requested, timeout=1000):
            action.trigger()

        button.set_listening(True)
        busy_menu = _get_context_menu(button)
        busy_action = next(
            item for item in busy_menu.actions() if item.data() == "screenshot"
        )
        assert not busy_action.isEnabled()

    def test_recent_action_signal_and_busy_state(self, qtbot, button):
        button.set_quick_actions(
            [("translate", "翻译"), ("translate", "重复"), ("rewrite", "改写")]
        )
        menu = _get_context_menu(button)
        action = next(item for item in menu.actions() if item.data() == "rewrite")
        with qtbot.waitSignal(button.quick_action_requested, timeout=1000) as spy:
            action.trigger()
        assert spy.args == ["rewrite"]

        button.set_responding(True)
        busy_menu = _get_context_menu(button)
        assert all(
            not item.isEnabled()
            for item in busy_menu.actions()
            if item.data() in {"translate", "rewrite"}
        )
        button.set_responding(False)
        button.set_pet_enabled(False)
        compact_menu = _get_context_menu(button)
        assert "最近快捷动作" not in [item.text() for item in compact_menu.actions()]

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

    def test_pet_click_does_not_require_window_focus(self, qtbot, button):
        from PyQt5.QtCore import Qt

        assert button.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert button.testAttribute(Qt.WA_ShowWithoutActivating)
        with qtbot.waitSignal(button.clicked, timeout=1000):
            qtbot.mouseClick(button, Qt.LeftButton)

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

    def test_pet_size_options_apply_and_invalid_value_falls_back(self, button):
        button.set_pet_size("small")
        assert button.pet_size == "small"
        assert (button.width(), button.height()) == (92, 97)

        button.set_pet_size("large")
        assert button.pet_size == "large"
        assert (button.width(), button.height()) == (140, 147)

        button.set_pet_size("unknown")
        assert button.pet_size == "medium"
        assert (button.width(), button.height()) == (116, 122)

    def test_reduce_motion_stops_periodic_animation(self, button):
        button.set_responding(True)
        assert button._animation_timer.isActive()
        button.set_reduce_motion(True)
        assert button.reduce_motion
        assert not button._animation_timer.isActive()
        assert button._animation_phase == 0
        assert button._hover_phase == 0
        button._advance_animation()
        assert button._animation_phase == 0

        button.set_reduce_motion(False)
        assert button._animation_timer.isActive()
        button.set_responding(False)

    def test_hidden_pet_stops_timers_and_show_restores_tracking(self, button):
        button._animation_timer.start()
        button._track_timer.start()
        button._result_timer.start(500)
        button.hide()
        assert not button._animation_timer.isActive()
        assert not button._track_timer.isActive()
        assert not button._result_timer.isActive()

        button.show()
        assert button._animation_timer.isActive()
        assert button._track_timer.isActive()

    def test_working_state_has_priority_over_capture_state(self, button):
        button.set_listening(True)
        assert button._effective_state() == "listening"
        button.set_responding(True)
        assert button._effective_state() == "working"
        button.set_responding(False)
        assert button._effective_state() == "listening"
        button.set_listening(False)
        assert button._effective_state() == "idle"

    def test_semantic_states_have_distinct_motion(self, button):
        button._animation_phase = 3
        idle = button._motion_for_state("idle")
        listening = button._motion_for_state("listening")
        working = button._motion_for_state("working")
        success = button._motion_for_state("success")
        error = button._motion_for_state("error")

        assert idle != listening
        assert working != success
        assert error[0] != 0

        button.set_reduce_motion(True)
        assert button._motion_for_state("working") == (0.0, 0.0, 0.0, 1.0)

    def test_idle_sprite_cycle_stays_subtle(self, button):
        phases = (3, 24, 49, 54, 78)
        motions = []
        for phase in phases:
            button._animation_phase = phase
            motions.append(button._motion_for_state("idle"))

        assert all(abs(motion[2]) == 0 for motion in motions)
        assert all(abs(motion[1]) <= 0.35 for motion in motions)
        assert all(motion[3] <= 1.001 for motion in motions)

    def test_hover_cycle_has_five_friendly_clips(self, button):
        button._hovered = True
        phases = (2, 6, 10, 14, 18)
        motions = []
        for phase in phases:
            button._hover_phase = phase
            motions.append(button._hover_motion())

        assert len(set(motions)) == len(phases)
        assert motions[0][1] < 0  # 致意
        assert motions[1][2] != 0  # 专注侧倾
        assert motions[2][1] < 0  # 开心回应
        assert motions[3][0] != 0  # 轻挥翅膀
        assert motions[4] == (0.0, 0.0, 0.0, 1.0)  # 回到稳定姿态

        button.set_reduce_motion(True)
        assert button._hover_motion() == (0.0, 0.0, 0.0, 1.0)

    def test_hover_animation_does_not_change_task_motion(self, button):
        button._animation_phase = 5
        before = button._motion_for_state("working")
        button._hovered = True
        button._hover_phase = 18
        after = button._motion_for_state("working")
        assert after == before

    def test_generated_sprite_sheets_load_five_consistent_frames(self, button):
        assert len(button._pet_idle_frames) == 5
        assert len(button._pet_hover_frames) == 5
        idle_sizes = {(frame.width(), frame.height()) for frame in button._pet_idle_frames}
        hover_sizes = {(frame.width(), frame.height()) for frame in button._pet_hover_frames}
        assert len(idle_sizes) == 1
        assert len(hover_sizes) == 1
        assert all(frame.toImage().pixelColor(0, 0).alpha() == 0 for frame in button._pet_idle_frames)
        assert all(frame.toImage().pixelColor(0, 0).alpha() == 0 for frame in button._pet_hover_frames)
        assert button._pet_for_state("working") == button._pet_content

        button._animation_phase = 27
        assert button._pet_for_state("idle") == button._pet_idle_frames[2]
        button._animation_phase = 49
        assert button._pet_for_state("idle") == button._pet_idle_frames[0]
        button._hovered = True
        button._hover_phase = 13
        assert button._pet_for_state("idle") == button._pet_hover_frames[3]

    def test_hover_animation_finishes_and_holds_last_frame(self, button):
        button._hovered = True
        button._hover_phase = 99
        button._advance_animation()
        assert button._hover_phase == 20
        assert button._pet_for_state("idle") == button._pet_hover_frames[4]

    def test_new_semantic_state_restarts_animation(self, button):
        button._animation_phase = 7
        button.set_listening(True)
        assert button._animation_phase == 0
        button._animation_phase = 7
        button.set_listening(False)
        button.set_responding(True)
        assert button._animation_phase == 0
        button._animation_phase = 7
        button.show_result(True)
        assert button._animation_phase == 0

    def test_result_state_returns_to_idle(self, qtbot, button):
        button.show_result(True)
        assert button._effective_state() == "success"
        button._result_timer.start(1)
        qtbot.waitUntil(lambda: button._effective_state() == "idle", timeout=100)

        button.show_result(False)
        assert button._effective_state() == "error"
