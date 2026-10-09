"""Shared pet interactions across all profiles, without installed user assets."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import QEvent, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QMouseEvent, QPainter, QPixmap
from PyQt5.QtWidgets import QLineEdit

from ai_desktop import config
from ai_desktop.ui.float_button import FloatButton

ROOT = Path(__file__).resolve().parents[1]


def mouse_event(pet, kind, delta=QPoint()):
    from PyQt5.QtCore import QPointF
    local = QPoint(40, 60) + delta
    return QMouseEvent(kind, QPointF(local), QPointF(pet.mapToGlobal(local)),
                       Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton,
                       Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton,
                       Qt.NoModifier)


def test_pointer_feedback_holds_during_drag_and_releases_without_click(pet):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(True))
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    assert pet._animator.layer == "interaction"
    assert pet._animator.next_wake_seconds is None
    pet.mouseMoveEvent(mouse_event(pet, QEvent.MouseMove, QPoint(40, 0)))
    assert pet._is_dragging
    assert pet._animator.current_animation == "grab"
    held = pet._animator.snapshot()
    pet.leaveEvent(QEvent(QEvent.Leave))
    assert pet._animator.snapshot() == held
    assert not pet._animation_timer.isActive()
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert clicked == []
    assert not pet._is_dragging
    assert pet._animator.current_animation == "release"
    pet._animator.tick(2)
    assert pet._animator.current_state == "idle"
    assert pet._animator.layer == "base"


def test_task_start_during_drag_and_reduced_motion_suppress_release(pet):
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    pet.mouseMoveEvent(mouse_event(pet, QEvent.MouseMove, QPoint(40, 0)))
    pet.set_responding(True)
    assert pet._animator.current_state == "working"
    assert pet._animator.layer == "base"  # No stalled transition while dragging.
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert pet._animator.current_state == "working"
    assert pet._animator.layer == "base"
    pet.set_responding(False)
    pet.set_reduce_motion(True)
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert pet._animator.layer == "base"
    assert not pet._animation_timer.isActive()


def test_click_response_keeps_existing_click_dispatch(pet):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(True))
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert clicked == [True]
    assert pet._animator.current_animation == "click"


@pytest.mark.parametrize("delta", [QPoint(1000, 1000), QPoint(-40, -60)], ids=["outside", "transparent"])
def test_releasing_a_press_outside_the_character_cancels_the_click(pet, delta):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(True))
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease, delta))
    assert clicked == []
    assert pet._animator.layer == "base"
    assert pet._animator.snapshot().frame_index == pet._animator.manifest.animations[
        pet._animator.manifest.states["idle"].base].frames[0]
    assert not pet.isDown() and pet._press_global is None
    assert pet._idle_wake_timer.isActive()


def test_orphan_release_does_not_play_a_click(pet):
    before = pet._animator.snapshot()
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert pet._animator.snapshot() == before


def test_another_button_release_does_not_drop_the_left_drag(pet):
    from PyQt5.QtCore import QPointF
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    local = QPointF(40, 60)
    right_release = QMouseEvent(QEvent.MouseButtonRelease, local,
                               QPointF(pet.mapToGlobal(local.toPoint())),
                               Qt.RightButton, Qt.LeftButton, Qt.NoModifier)
    pet.mouseReleaseEvent(right_release)
    assert pet._press_global is not None
    pet.mouseMoveEvent(mouse_event(pet, QEvent.MouseMove, QPoint(40, 0)))
    assert pet._is_dragging
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert not pet._is_dragging and pet._animator.current_animation == "release"


def test_passing_pointer_never_starts_a_greeting(qtbot, pet):
    before = pet._animator.snapshot()
    pet.enterEvent(QEvent(QEvent.Enter))
    assert pet._hover_reaction_timer.isActive()
    assert pet._animator.snapshot() == before
    pet.leaveEvent(QEvent(QEvent.Leave))
    qtbot.wait(190)
    assert not pet._hover_reaction_timer.isActive()
    assert pet._animator.snapshot() == before
    assert pet._last_hover_reaction_time == float("-inf")


def test_hover_dwell_reacts_once_and_task_cancels_pending_response(qtbot, pet):
    pet.enterEvent(QEvent(QEvent.Enter))
    qtbot.waitUntil(lambda: pet._animator.layer == "reaction", timeout=500)
    assert pet._last_hover_reaction_time != float("-inf")
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet.enterEvent(QEvent(QEvent.Enter))
    pet.set_responding(True)
    assert not pet._hover_reaction_timer.isActive()
    qtbot.wait(190)
    assert pet._animator.current_state == "working"


def test_hover_dwell_cannot_release_a_pressed_pose(qtbot, pet):
    pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
    held = pet._animator.snapshot()
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet.enterEvent(QEvent(QEvent.Enter))
    qtbot.wait(190)
    assert pet._animator.snapshot() == held
    assert pet._animator.next_wake_seconds is None
    pet.mouseReleaseEvent(mouse_event(pet, QEvent.MouseButtonRelease))
    assert pet._animator.current_animation == "click"


def test_rescheduling_preserves_original_idle_deadline(pet):
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=100):
        pet._last_animation_time = 100
        pet._animator._ambient_delay = 8
        pet._sync_animation_timer()
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=105):
        pet._sync_animation_timer()
        assert pet._idle_wake_timer.interval() == 3000  # Coarse timers may report up to 5% slack.
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=108):
        pet._advance_animation()
    assert pet._animator.layer == "ambient"
    assert pet._animator._frame_timer == pytest.approx(0)


def test_tool_activity_updates_keep_elapsed_transition_time(pet):
    with patch("ai_desktop.ui.float_button.time.monotonic", return_value=100):
        pet._last_animation_time = 100
        pet.set_responding(True)
    animation = pet._animator.current_animation
    for now, state in ((100.01, "searching"), (100.02, "executing"), (100.03, "working")):
        with patch("ai_desktop.ui.float_button.time.monotonic", return_value=now):
            pet.set_task_activity(state)
        assert pet._animator.current_animation == animation
    assert pet._animator._frame_timer == pytest.approx(.03)


@pytest.mark.parametrize("layer", ["ambient", "reaction"])
def test_hide_reduce_and_compact_mode_clear_secondary_pose(pet, layer):
    for reset in (lambda: pet.set_reduce_motion(True), lambda: pet.hide(),
                  lambda: pet.set_pet_enabled(False)):
        pet.set_reduce_motion(False)
        pet.set_pet_enabled(True)
        pet.show()
        pet._animator.set_state("idle", restart=True, animate=False)
        pet._animator.tick(0, elapsed_seconds=60)
        pet._animator._start_animation(pet._animator.manifest.interactions["signature"], layer)
        pet._animator.tick(.25)
        if pet._animator.manifest.secondary_motion:
            assert any(pet._animator.snapshot().secondary_angles)
        reset()
        assert pet._animator.snapshot().secondary_angles == ()
        assert not pet._animation_timer.isActive()


def test_hide_and_compact_mode_cancel_a_held_pointer_pose(pet):
    for hide in (True, False):
        pet.mousePressEvent(mouse_event(pet, QEvent.MouseButtonPress))
        assert pet._animator.layer == "interaction"
        if hide:
            pet.hide()
            pet.show()
        else:
            pet.set_pet_enabled(False)
            pet.set_pet_enabled(True)
        assert pet._animator.layer != "interaction"
        assert not pet._is_dragging
        assert not pet.isDown()


@pytest.fixture(params=["owl-v2", "astra", "boba", "shinchan"])
def pet(request, qtbot, tmp_path, monkeypatch):
    name = request.param
    monkeypatch.setattr(config, "PET_SOURCE", "built-in")
    monkeypatch.setattr(config, "PET_NAME", "owl-v2")
    if name != "owl-v2":
        # Exercise the real per-character manifests with a neutral fixture
        # atlas. CI does not depend on ~/.petdex or redistribute its artwork.
        data = json.loads((ROOT / f"ai_desktop/pets/petdex-profiles/{name}.json").read_text())
        ss = data["spritesheet"]
        atlas = QPixmap(ss["columns"] * ss["frame_width"],
                        ((ss["frame_count"] + ss["columns"] - 1) // ss["columns"]) * ss["frame_height"])
        atlas.fill(Qt.transparent)
        painter = QPainter(atlas)
        for index in range(ss["frame_count"]):
            x, y = index % ss["columns"] * ss["frame_width"], index // ss["columns"] * ss["frame_height"]
            painter.fillRect(x + 50, y + 50, 80, 140, QColor(30 + index, 110, 150))
        painter.end()
        data["spritesheet"]["file"] = "atlas.png"
        atlas.save(str(tmp_path / "atlas.png"))
        path = tmp_path / "pet.json"
        path.write_text(json.dumps(data))
        monkeypatch.setattr("ai_desktop.ui.float_button._get_pet_manifest_path", lambda *args: str(path))
    with patch("ai_desktop.ui.float_button.pin_to_all_spaces"):
        button = FloatButton()
        qtbot.addWidget(button)
        button.show()
        button.set_follow_cursor_screen(False)
        yield button
        button.close()
        button._motion_preference.close()


def show_toolbar(pet):
    pet.enterEvent(QEvent(QEvent.Enter))
    pet._show_toolbar()
    return pet._toolbar


def test_hover_delay_bridge_and_leave(qtbot, pet):
    pet.enterEvent(QEvent(QEvent.Enter))
    assert pet._toolbar_show_timer.interval() == 350
    assert not pet._toolbar.isVisible()
    pet.leaveEvent(QEvent(QEvent.Leave))
    assert not pet._toolbar_show_timer.isActive()
    pet.enterEvent(QEvent(QEvent.Enter))
    qtbot.waitUntil(pet._toolbar.isVisible, timeout=700)
    pet.leaveEvent(QEvent(QEvent.Leave))
    assert pet._toolbar.isVisible()  # 8px bridge has a grace period.
    pet._toolbar.enterEvent(QEvent(QEvent.Enter))
    assert not pet._toolbar_hide_timer.isActive()
    pet._maybe_hide_toolbar()
    assert pet._toolbar.isVisible()
    pet._toolbar.leaveEvent(QEvent(QEvent.Leave))
    qtbot.waitUntil(lambda: not pet._toolbar.isVisible(), timeout=700)


def test_toolbar_and_its_gap_keep_attention_without_replaying_a_greeting(pet):
    toolbar = show_toolbar(pet)
    pet._react_to_hover()
    pet._animator.tick(2)
    last_greeting = pet._last_hover_reaction_time
    pet.leaveEvent(QEvent(QEvent.Leave))
    toolbar.enterEvent(QEvent(QEvent.Enter))
    pet._animator.tick(15)
    pet._sync_animation_timer()
    assert pet._animator.current_state == "hover"
    assert pet._last_hover_reaction_time == last_greeting
    assert not pet._animation_timer.isActive() and not pet._idle_wake_timer.isActive()
    toolbar.leaveEvent(QEvent(QEvent.Leave))
    assert pet._animator.current_state == "hover"  # Preserve attention across the 8px gap.
    pet._maybe_hide_toolbar()
    pet._animator.tick(.6)
    assert pet._animator.current_state == "idle"
    assert pet._animator.layer == "base" and pet._animator.next_wake_seconds is not None


def test_task_and_reduced_mode_own_pose_while_using_toolbar(pet):
    toolbar = show_toolbar(pet)
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet.set_responding(True)
    pet.set_task_activity("waiting")
    toolbar.enterEvent(QEvent(QEvent.Enter))
    pet._animator.tick(1)
    pet._sync_animation_timer()
    assert pet._animator.current_state == "waiting"
    assert not pet._animation_timer.isActive() and not pet._idle_wake_timer.isActive()
    pet.set_reduce_motion(True)
    toolbar.leaveEvent(QEvent(QEvent.Leave))
    pet._maybe_hide_toolbar()
    assert pet._animator.current_state == "waiting"
    assert not pet._animation_timer.isActive() and not pet._idle_wake_timer.isActive()


def test_toolbar_action_restores_idle_without_another_pointer_event(pet):
    toolbar = show_toolbar(pet)
    pet.leaveEvent(QEvent(QEvent.Leave))
    toolbar.enterEvent(QEvent(QEvent.Enter))
    pet._animator.tick(.6)
    toolbar._activate(0)  # The toolbar hides itself before notifying the pet.
    pet._animator.tick(.6)
    assert not toolbar.isVisible()
    assert pet._animator.current_state == "idle" and pet._animator.layer == "base"
    assert pet._animator.next_wake_seconds is not None


def test_toolbar_does_not_activate_or_lose_selection(qtbot, pet):
    editor = QLineEdit("retain this selection")
    qtbot.addWidget(editor)
    editor.show()
    editor.activateWindow()
    editor.setFocus()
    editor.selectAll()
    toolbar = show_toolbar(pet)
    assert toolbar.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert toolbar.testAttribute(Qt.WA_ShowWithoutActivating)
    with qtbot.waitSignal(pet.read_selection_requested):
        qtbot.mouseClick(toolbar.buttons[2], Qt.LeftButton)
    assert editor.selectedText() == "retain this selection"
    assert all(button.focusPolicy() == Qt.NoFocus for button in toolbar.buttons)


def test_task_states_update_actions_and_wait_without_polling(qtbot, pet):
    toolbar = show_toolbar(pet)
    assert [button.text() for button in toolbar.buttons] == ["对话", "截图", "朗读"]
    pet.set_responding(True)
    for state in ("searching", "executing", "waiting"):
        pet.set_task_activity(state)
        assert pet._effective_state() == state
        assert pet._animator.current_state == state
        assert toolbar.status.text() == pet.toolTip()
        assert not toolbar.buttons[2].isVisible()
    assert toolbar.buttons[0].text() == "查看确认"
    pet._animator.tick(.6)
    pet._sync_animation_timer()
    assert not pet._animation_timer.isActive()
    assert not pet._idle_wake_timer.isActive()
    with qtbot.waitSignal(pet.stop_task_requested):
        qtbot.mouseClick(toolbar.buttons[1], Qt.LeftButton)
    pet.set_responding(False)
    pet.set_task_activity("waiting")
    assert pet._effective_state() == "idle"


def test_stale_capture_action_cannot_start_during_task(pet):
    calls = []
    pet.screenshot_requested.connect(lambda: calls.append("screenshot"))
    pet.read_selection_requested.connect(lambda: calls.append("read"))
    pet.set_responding(True)
    pet._on_toolbar_action("screenshot")
    pet._on_toolbar_action("read")
    assert not calls


def test_unread_survives_short_feedback_and_clears_on_acknowledgement(pet):
    pet.show_result(True)
    pet.mark_result_unread("success")
    pet._clear_result_state()
    assert pet._effective_state() == "idle"
    assert "未查看" in pet.toolTip()
    toolbar = show_toolbar(pet)
    assert toolbar.buttons[0].text() == "查看结果"
    pet.mark_result_unread(None)
    assert "未查看" not in pet.toolTip()
    assert toolbar.buttons[0].text() == "对话"
    pet.show_cancelled()
    assert pet._effective_state() == "cancelled"
    assert "停止" in pet.toolTip()


def test_badges_keep_transparent_corners_click_through(pet):
    for size in ("small", "medium", "large"):
        pet.set_pet_size(size)
        pet.set_responding(True)
        for state in ("working", "searching", "executing", "waiting"):
            pet.set_task_activity(state)
            assert not pet.mask().contains(QPoint(0, 0))
            assert pet.mask().contains(pet._badge_rect().center().toPoint())
            image = pet.grab().toImage()
            assert all(image.pixelColor(x, y).alpha() < 24 or pet.mask().contains(QPoint(x, y))
                       for y in range(image.height()) for x in range(image.width()))
        pet.set_responding(False)
        for succeeded in (True, False):
            pet.show_result(succeeded)
            assert not pet.mask().contains(QPoint(0, 0))
            for phase in range(8):
                pet._animation_phase = phase
                image = pet.grab().toImage()
                assert all(image.pixelColor(x, y).alpha() < 24 or pet.mask().contains(QPoint(x, y))
                           for y in range(image.height()) for x in range(image.width()))
            pet._clear_result_state()


def test_toolbar_fits_screen_edges_and_reloads_close_it(pet):
    area = QRect(0, 0, 800, 600)
    toolbar = show_toolbar(pet)
    for point in (QPoint(0, 0), QPoint(700, 0), QPoint(0, 490), QPoint(700, 490)):
        toolbar.position_near(QRect(point, pet.size()), area)
        assert area.contains(toolbar.geometry())
    pet.set_responding(True)
    pet.set_task_activity("waiting")
    pet.reload_pet()
    assert not toolbar.isVisible()
    assert pet._effective_state() == "waiting"
    assert pet._animator.current_state == "waiting"
    pet.set_reduce_motion(True)
    assert not pet._animation_timer.isActive()
    assert pet._effective_state() == "waiting"


def test_unknown_pet_falls_back_to_existing_poses(pet):
    manifest = pet._animator.manifest
    states = {key: value for key, value in manifest.states.items()
              if key not in {"searching", "executing", "waiting", "cancelled", "hover"}}
    pet._animator._manifest = replace(manifest, states=states)
    pet.set_responding(True)
    pet.set_task_activity("searching")
    assert pet._animator.current_state == "working"
    pet.set_task_activity("waiting")
    assert pet._animator.current_state == "idle"


def test_screen_follow_requires_dwell_and_resets_on_return(pet):
    pet._follow_cursor_screen_enabled = True
    pet._hovered = False
    current, target = MagicMock(), MagicMock()
    target.availableGeometry.return_value = QRect(1440, 0, 800, 600)
    initial = pet.pos()
    with patch("ai_desktop.ui.float_button.QApplication.screenAt", side_effect=[target, current] * 3), \
            patch("ai_desktop.ui.float_button.time.monotonic", side_effect=[100., 100.3, 101.]):
        pet._follow_cursor_screen()
        pet._follow_cursor_screen()
        assert pet.pos() == initial
        pet._follow_cursor_screen()
    assert pet.x() == 2239 - pet.width() - 20
    with patch("ai_desktop.ui.float_button.QApplication.screenAt", side_effect=[current, current]):
        pet._follow_cursor_screen()
    assert pet._follow_candidate is None
