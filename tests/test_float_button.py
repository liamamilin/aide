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
        btn._idle_wake_timer.stop()
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
        assert action_texts[:2] == ["🔊 朗读选区", "截图到对话…"]
        assert action_texts[2:6] == ["最近快捷动作", "⚡  翻译", "⚡  解释", "⚡  改写"]
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
        assert button._spritesheet_available
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
        button._advance_animation()
        assert button._animation_phase == 0

        button.set_reduce_motion(False)
        assert button._animation_timer.isActive()
        button.set_responding(False)

    def test_hidden_pet_stops_timers_and_show_restores_tracking(self, button):
        button._animation_timer.start()
        button._idle_wake_timer.start(500)
        button._track_timer.start()
        button._result_timer.start(500)
        button.hide()
        assert not button._animation_timer.isActive()
        assert not button._idle_wake_timer.isActive()
        assert not button._track_timer.isActive()
        assert not button._result_timer.isActive()

        button.show()
        assert not button._animation_timer.isActive()
        assert button._idle_wake_timer.isActive()
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

    def test_hover_greeting_keeps_geometry_and_horizontal_anchor(self, button):
        from PyQt5.QtCore import QEvent
        original = button.geometry()
        button.enterEvent(QEvent(QEvent.Enter))
        for _ in range(20):
            button._animator.tick(.04)
            button._update_spritesheet_frame()
            assert button.geometry() == original
            assert button._spritesheet_draw_rect().center().x() == button.width() / 2

    def test_leaving_finishes_gesture_before_return_to_idle(self, button):
        from PyQt5.QtCore import QEvent
        button.enterEvent(QEvent(QEvent.Enter))
        button._animator.tick(.1)
        before = button._animator.snapshot()
        button.leaveEvent(QEvent(QEvent.Leave))
        assert button._animator.snapshot() == before
        button._animator.tick(2)
        button._sync_animation_timer()
        assert button._animator.current_state == "idle"
        assert not button._animation_timer.isActive()
        assert button._idle_wake_timer.isActive()

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

    def test_hover_and_task_pause_idle_blink(self, button):
        button._sync_animation_timer()
        assert button._idle_wake_timer.isActive()
        from PyQt5.QtCore import QEvent

        button.enterEvent(QEvent(QEvent.Enter))
        assert not button._idle_wake_timer.isActive()
        assert button._animation_timer.isActive()
        button._hovered = False
        button.set_listening(True)
        assert not button._idle_wake_timer.isActive()
        button.set_listening(False)
        assert button._idle_wake_timer.isActive()

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


def test_real_clock_drives_idle_wake_and_blink(button):
    # Simulate a real single-shot timer callback at its scheduled deadline.
    button._sync_animation_timer()
    animator = button._animator
    deadline = animator.next_wake_seconds
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=button._last_animation_time + deadline):
        button._advance_animation()
    assert animator.layer == "ambient"
    assert button._animation_timer.isActive()
    assert not button._idle_wake_timer.isActive()


def test_hover_finishes_and_task_interrupts_without_resetting_progress(button):
    from PyQt5.QtCore import QEvent
    button.enterEvent(QEvent(QEvent.Enter))
    assert button._animator.current_state == "hover"
    assert button._animator.layer == "reaction"
    button._animator.tick(2)
    button._sync_animation_timer()
    assert not button._animation_timer.isActive()
    assert not button._idle_wake_timer.isActive()
    button.set_responding(True)
    assert button._animator.current_state == "working"
    button.show_result(False)
    assert button._effective_state() == "working"
    assert button._animator.current_state == "working"


def test_reduce_motion_keeps_semantic_pose_and_badge(button):
    button.set_responding(True)
    button.set_reduce_motion(True)
    assert button._animator.current_state == "working"
    assert button._effective_state() == "working"
    assert not button._animation_timer.isActive()
    assert not button._idle_wake_timer.isActive()


def test_system_motion_change_preserves_user_preference(button):
    button._motion_preference.changed.emit(True)
    assert button.reduce_motion
    assert not button._idle_wake_timer.isActive()
    button.set_reduce_motion(True)
    button._motion_preference.changed.emit(False)
    assert button.reduce_motion
    button.set_reduce_motion(False)
    assert not button.reduce_motion
    assert button._idle_wake_timer.isActive()


def test_corrupt_custom_pet_falls_back_without_losing_requested_mode(button, tmp_path):
    from ai_desktop import config
    manifest = tmp_path / "bad.json"
    manifest.write_text('{"schema_version": 99}', encoding="utf-8")
    from ai_desktop.ui.float_button import _get_pet_manifest_path
    with patch.object(config, "PET_SOURCE", "petdex"), \
            patch("ai_desktop.ui.float_button._get_pet_manifest_path", side_effect=[
                str(manifest), _get_pet_manifest_path("built-in", "owl-v2")]):
        button.reload_pet()
    assert button.pet_enabled
    assert button._animator.current_state == "idle"


def test_all_frames_fit_one_stable_hit_region(button):
    from PyQt5.QtCore import QPoint
    for size in ("small", "medium", "large"):
        button.set_pet_size(size)
        mask = button.mask()
        for index in range(button._spritesheet.frame_count):
            button._spritesheet_frame = button._spritesheet.frame(index)
            image = button.grab().toImage()
            assert all(image.pixelColor(x, y).alpha() <= 16 or mask.contains(QPoint(x, y))
                       for y in range(image.height()) for x in range(image.width()))


def test_idle_eye_poses_do_not_redraw_or_move_body(button):
    # Every idle pose has identical pixels outside the eye region, including
    # feather edges, laptop and feet. This catches body jitter at asset level.
    images = [button._spritesheet.frame(index).toImage() for index in range(4)]
    bounds = []
    for image in images[1:]:
        points = [(x, y) for y in range(image.height()) for x in range(image.width())
                  if image.pixel(x, y) != images[0].pixel(x, y)]
        assert points
        bounds.append((min(x for x, _ in points), min(y for _, y in points),
                       max(x for x, _ in points), max(y for _, y in points)))
    assert all(65 < left < right < 250 and 95 < top < bottom < 180
               for left, top, right, bottom in bounds)


def test_rapid_pointer_reentry_does_not_restart_greeting(button):
    from PyQt5.QtCore import QEvent
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=100):
        button.enterEvent(QEvent(QEvent.Enter))
    button._animator.tick(.1)
    before = button._animator.snapshot()
    button.leaveEvent(QEvent(QEvent.Leave))
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=100.1):
        button.enterEvent(QEvent(QEvent.Enter))
    assert button._animator.snapshot() == before
    assert button._last_hover_reaction_time == 100
    button._animator.tick(2)
    assert button._animator.current_state == "hover"
    assert button._animator.next_wake_seconds is None


def test_context_menu_pauses_animation_and_resumes_after_close(qtbot, button):
    from unittest.mock import Mock

    from PyQt5.QtCore import QPoint
    event = Mock()
    event.globalPos.return_value = QPoint(100, 100)
    button.contextMenuEvent(event)
    menu = button._context_menu
    assert menu.isVisible()
    assert button._menu_open
    assert not button._animation_timer.isActive()
    assert not button._idle_wake_timer.isActive()
    menu.close()
    qtbot.waitUntil(lambda: not button._menu_open)
    assert button._idle_wake_timer.isActive()
