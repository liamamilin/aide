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
        btn._idle_blink_timer.stop()
        btn._blink_open_timer.stop()
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
        assert action_texts[:3] == ["🔊 朗读选区", "", "截图到对话…"]
        assert action_texts[3:7] == ["", "最近快捷动作", "⚡  翻译", "⚡  解释"]
        assert action_texts[7] == "⚡  改写"
        assert "设置…" in action_texts
        assert "桌面宠物形态" in action_texts
        assert "隐藏桌面宠物" in action_texts
        assert "退出" in action_texts

    def test_read_selection_action_emits_signal(self, qtbot, button):
        menu = _get_context_menu(button)
        action = next(item for item in menu.actions() if item.text() == "🔊 朗读选区")
        with qtbot.waitSignal(button.read_selection_requested, timeout=1000):
            action.trigger()

    def test_speaking_menu_offers_stop_and_disables_other_tasks(self, qtbot, button):
        button.set_speaking(True)
        menu = _get_context_menu(button)
        actions = menu.actions()
        stop_action = next(item for item in actions if item.text() == "■ 停止朗读")
        screenshot_action = next(item for item in actions if item.data() == "screenshot")

        assert button.toolTip() == "正在朗读 · 右键可停止"
        assert stop_action.isEnabled()
        assert not screenshot_action.isEnabled()
        with qtbot.waitSignal(button.stop_speech_requested, timeout=1000):
            stop_action.trigger()

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

    def test_screen_follow_menu_can_reenable_after_manual_placement(self, qtbot, button):
        action = next(
            item for item in _get_context_menu(button).actions()
            if item.text() == "跟随鼠标所在屏幕"
        )
        assert action.isChecked()
        with qtbot.waitSignal(button.screen_follow_changed, timeout=1000) as spy:
            action.toggle()
        assert spy.args == [False]
        assert not button.follow_cursor_screen
        assert "已固定屏幕" in button.toolTip()
        assert not button._track_timer.isActive()

        with patch.object(button, "_follow_cursor_screen") as follow:
            action = next(
                item for item in _get_context_menu(button).actions()
                if item.text() == "跟随鼠标所在屏幕"
            )
            assert not action.isChecked()
            with qtbot.waitSignal(button.screen_follow_changed, timeout=1000) as spy:
                action.toggle()
            assert spy.args == [True]
            follow.assert_called_once_with()
            assert "拖动可固定位置" in button.toolTip()
            assert button._track_timer.isActive()

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
        assert (button.width(), button.height()) == (104, 110)

    def test_idle_hit_mask_excludes_empty_corners_without_clipping_pet(self, button):
        from PyQt5.QtCore import QPoint

        for size in ("small", "medium", "large"):
            button.set_pet_size(size)
            mask = button.mask()
            assert not mask.contains(QPoint(0, 0))
            assert not mask.contains(QPoint(button.width() - 1, 0))
            assert mask.contains(QPoint(button.width() // 2, button.height() // 2))
            for phase in (0, 4):
                button._hovered = phase > 0
                button._hover_phase = phase
                image = button.grab().toImage()
                assert all(
                    image.pixelColor(x, y).alpha() <= 16 or mask.contains(QPoint(x, y))
                    for y in range(image.height())
                    for x in range(image.width())
                )

        button.set_responding(True)
        assert button.mask().contains(QPoint(0, 0))
        button.set_responding(False)
        assert not button.mask().contains(QPoint(0, 0))
        button.set_pet_enabled(False)
        assert not button.mask().contains(QPoint(0, 0))
        assert button.mask().contains(QPoint(button.width() // 2, button.height() // 2))

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
        button._idle_blink_timer.start(500)
        button._track_timer.start()
        button._result_timer.start(500)
        button.hide()
        assert not button._animation_timer.isActive()
        assert not button._idle_blink_timer.isActive()
        assert not button._blink_open_timer.isActive()
        assert not button._track_timer.isActive()
        assert not button._result_timer.isActive()

        button.show()
        assert not button._animation_timer.isActive()
        assert button._idle_blink_timer.isActive()
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

    def test_hover_greeting_has_stable_horizontal_anchor(self, button):
        button._hovered = True
        phases = (0, 2, 4, 6, 8)
        motions = []
        for phase in phases:
            button._hover_phase = phase
            motions.append(button._hover_motion())

        assert all(motion[0] == 0.0 and motion[2] == 0.0 for motion in motions)
        assert all(motion[1] < 0 for motion in motions[1:4])
        assert motions[0] == motions[4] == (0.0, 0.0, 0.0, 1.0)

        button.set_reduce_motion(True)
        assert button._hover_motion() == (0.0, 0.0, 0.0, 1.0)

    def test_hover_motion_is_eased_without_pose_overshoot(self, button):
        button._hovered = True
        sampled = []
        for phase in range(9):
            button._hover_phase = phase
            sampled.append(button._hover_motion())
        assert all(abs(sampled[index + 1][1] - sampled[index][1]) < 0.65 for index in range(len(sampled) - 1))

    def test_hover_leave_settles_without_snapping(self, button):
        from PyQt5.QtCore import QEvent

        button._hovered = True
        button._hover_phase = 4
        previous_y = button._hover_motion()[1]
        button.leaveEvent(QEvent(QEvent.Leave))
        positions = [button._hover_motion()[1]]
        for _ in range(4):
            button._advance_animation()
            positions.append(button._hover_motion()[1])

        assert positions[0] == previous_y
        assert positions[-1] == 0.0
        assert all(positions[index] <= positions[index + 1] for index in range(4))
        assert max(positions[index + 1] - positions[index] for index in range(4)) < 0.65
        assert not button._animation_timer.isActive()
        assert button._idle_blink_timer.isActive()

    def test_hover_animation_does_not_change_task_motion(self, button):
        button._animation_phase = 5
        before = button._motion_for_state("working")
        button._hovered = True
        button._hover_phase = 18
        after = button._motion_for_state("working")
        assert after == before

    def test_task_transition_discards_partial_hover_greeting(self, button):
        button._hovered = True
        button._hover_phase = 4
        button._hover_return_phase = 1
        button.set_responding(True)

        assert button._hover_phase == 8
        assert button._hover_return_phase == 4
        assert button._hover_motion() == (0.0, 0.0, 0.0, 1.0)
        button.set_responding(False)
        assert button._pet_for_state("idle") == button._pet_attentive_content

    def test_hover_while_busy_starts_from_settled_pose(self, button):
        from PyQt5.QtCore import QEvent

        button.set_responding(True)
        button.enterEvent(QEvent(QEvent.Enter))
        assert button._hover_phase == 8
        button.set_responding(False)
        assert button._hover_motion() == (0.0, 0.0, 0.0, 1.0)
        assert button._pet_for_state("idle") == button._pet_attentive_content

    def test_drag_has_threshold_and_does_not_open_dialog(self, button):
        from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent

        def mouse_event(kind, local, global_pos, button_type, buttons):
            return QMouseEvent(
                kind, QPointF(local), QPointF(global_pos),
                button_type, buttons, Qt.NoModifier,
            )

        clicked = []
        button.clicked.connect(lambda: clicked.append(True))
        local = QPoint(40, 45)
        origin = button.pos()
        global_pos = button.mapToGlobal(local)
        button.mousePressEvent(mouse_event(
            QEvent.MouseButtonPress, local, global_pos,
            Qt.LeftButton, Qt.LeftButton,
        ))
        small_delta = QPoint(2, 0)
        button.mouseMoveEvent(mouse_event(
            QEvent.MouseMove, local + small_delta, global_pos + small_delta,
            Qt.NoButton, Qt.LeftButton,
        ))
        assert not button._is_dragging
        assert button.pos() == origin
        assert button.follow_cursor_screen

        drag_delta = QPoint(-40, 0)
        button.mouseMoveEvent(mouse_event(
            QEvent.MouseMove, local + drag_delta, global_pos + drag_delta,
            Qt.NoButton, Qt.LeftButton,
        ))
        assert button._is_dragging
        assert button.cursor().shape() == Qt.ClosedHandCursor
        assert button.pos() != origin
        assert not button.follow_cursor_screen
        button.mouseReleaseEvent(mouse_event(
            QEvent.MouseButtonRelease, local + drag_delta, global_pos + drag_delta,
            Qt.LeftButton, Qt.NoButton,
        ))
        assert clicked == []
        assert not button._is_dragging
        assert button.cursor().shape() == Qt.PointingHandCursor

    def test_pinned_pet_does_not_query_cursor_screen(self, button):
        button.set_follow_cursor_screen(False)
        with patch("ai_desktop.ui.float_button.QCursor.pos") as cursor_pos:
            button._follow_cursor_screen()
        cursor_pos.assert_not_called()
        button.hide()
        button.show()
        assert not button._track_timer.isActive()

    def test_reenabling_follow_moves_to_cursor_screen(self, button):
        from unittest.mock import MagicMock

        from PyQt5.QtCore import QPoint, QRect

        current = MagicMock()
        target = MagicMock()
        target.availableGeometry.return_value = QRect(1440, 0, 800, 600)
        button.set_follow_cursor_screen(False)
        with patch("ai_desktop.ui.float_button.QCursor.pos", return_value=QPoint(1800, 250)), \
                patch("ai_desktop.ui.float_button.QApplication.screenAt", side_effect=[target, current]):
            button.set_follow_cursor_screen(True)
        geometry = target.availableGeometry.return_value
        assert button.pos() == QPoint(
            geometry.right() - button.width() - 20,
            geometry.center().y() - button.height() // 2,
        )
        assert button._track_timer.isActive()

    def test_idle_blink_keeps_same_canvas_and_anchor(self, button):
        from ai_desktop.ui.float_button import _visible_pet_bounds

        poses = (
            button._pet_content,
            button._pet_blink_content,
            button._pet_attentive_content,
            button._pet_focused_content,
        )
        assert len({(pose.width(), pose.height()) for pose in poses}) == 1
        bounds = [
            _visible_pet_bounds(frame)
            for frame in poses
        ]
        assert all(bounds[0] == bounds[index] for index in range(1, len(bounds)))
        assert button._pet_content.width() < button._pet_source.width()
        assert button._pet_for_state("working") == button._pet_content

        # Every expression reuses the canonical body's pixels outside the eye masks.
        for x, y in ((605, 320), (605, 680), (340, 850), (610, 880), (480, 1070)):
            px = x - button._pet_crop.x()
            py = y - button._pet_crop.y()
            color = poses[0].toImage().pixelColor(px, py)
            assert all(pose.toImage().pixelColor(px, py) == color for pose in poses[1:])

        button._idle_blinking = True
        assert button._pet_for_state("idle") == button._pet_blink_content
        button._idle_blinking = False
        assert button._pet_for_state("idle") == button._pet_content
        button._hovered = True
        button._hover_phase = 3
        assert button._pet_for_state("idle") == button._pet_attentive_content
        button._hover_phase = 4
        assert button._pet_for_state("idle") == button._pet_blink_content
        button._hover_phase = 5
        assert button._pet_for_state("idle") == button._pet_attentive_content
        button._hovered = False
        button._working_intro_phase = 1
        assert button._pet_for_state("working") == button._pet_focused_content
        button.set_reduce_motion(True)
        assert button._pet_for_state("idle") == button._pet_content
        assert button._pet_for_state("working") == button._pet_content

    def test_idle_blink_waits_then_returns_to_still_frame(self, button):
        button._blink_rng.seed(7)
        button._sync_animation_timer()
        first_delay = button._idle_blink_timer.remainingTime()
        assert 3800 <= first_delay <= 7600
        assert not button._animation_timer.isActive()

        button._idle_blink_timer.stop()
        button._begin_idle_blink()
        assert button._pet_for_state("idle") == button._pet_blink_content
        assert button._blink_open_timer.isActive()
        button._blink_open_timer.stop()
        button._end_idle_blink()
        assert button._pet_for_state("idle") == button._pet_content
        assert button._idle_blink_timer.isActive()
        assert button._idle_blink_timer.remainingTime() != first_delay

    def test_idle_blink_timer_opens_eyes_again(self, qtbot, button):
        button._idle_blink_timer.start(1)
        qtbot.waitUntil(lambda: button._idle_blinking, timeout=500)
        assert button._pet_for_state("idle") == button._pet_blink_content
        qtbot.waitUntil(lambda: not button._idle_blinking, timeout=500)
        assert button._pet_for_state("idle") == button._pet_content
        assert button._idle_blink_timer.isActive()

    def test_hover_and_task_pause_idle_blink(self, button):
        button._sync_animation_timer()
        assert button._idle_blink_timer.isActive()
        button._hovered = True
        button._hover_phase = 0
        button._sync_animation_timer()
        assert not button._idle_blink_timer.isActive()
        assert button._animation_timer.isActive()
        button._hovered = False
        button.set_listening(True)
        assert not button._idle_blink_timer.isActive()
        button.set_listening(False)
        assert button._idle_blink_timer.isActive()

    def test_hover_animation_finishes_on_stable_frame(self, button):
        button._hovered = True
        button._hover_phase = 99
        button._advance_animation()
        assert button._hover_phase == 8
        assert button._hover_motion() == (0.0, 0.0, 0.0, 1.0)
        assert button._pet_for_state("idle") == button._pet_attentive_content

    def test_working_intro_settles_and_keeps_focused_eyes(self, button):
        button.set_responding(True)
        assert button._pet_for_state("working") == button._pet_content
        button._advance_animation()
        assert button._pet_for_state("working") == button._pet_focused_content
        for _ in range(8):
            button._advance_animation()
        assert button._motion_for_state("working") == (0.0, 0.0, 0.0, 1.0)
        button._animation_phase = 95
        button._advance_animation()
        assert button._motion_for_state("working") == (0.0, 0.0, 0.0, 1.0)

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
