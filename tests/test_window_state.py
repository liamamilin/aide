"""Window placement persistence and recovery regressions."""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import QPoint, QRect, QSize

from ai_desktop import config
from ai_desktop.config import Agent
from ai_desktop.main import ChatController
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.float_button import FloatButton
from ai_desktop.utils.window_state import (
    ScreenArea,
    WindowState,
    fit_window_state,
    parse_window_state,
    serialize_window_state,
)


def test_window_state_round_trip_and_bad_config_fallback():
    raw = serialize_window_state(QRect(-1200, 40, 520, 700), "External", include_size=True)
    assert parse_window_state(raw, include_size=True) == WindowState(-1200, 40, 520, 700, "External")

    bad_values = [
        "not-json",
        "{}",
        json.dumps({"version": 1, "x": True, "y": 1, "width": 400, "height": 500}),
        json.dumps({"version": 1, "x": 1, "y": 1, "width": 0, "height": 500}),
        json.dumps({"version": 1, "x": 2_000_000, "y": 1, "width": 400, "height": 500}),
    ]
    assert all(parse_window_state(value, include_size=True) is None for value in bad_values)


def test_missing_external_screen_is_clamped_to_nearest_available_screen():
    screens = [ScreenArea("Built-in", QRect(0, 0, 1440, 900))]
    fitted = fit_window_state(
        WindowState(1800, 200, 520, 700, "Detached"),
        screens,
        fallback_size=QSize(440, 580),
        minimum_size=QSize(400, 460),
    )
    assert fitted is not None
    rect, screen = fitted
    assert screen.name == "Built-in"
    assert screens[0].geometry.contains(rect)
    assert rect == QRect(920, 200, 520, 700)


def test_named_screen_wins_and_small_screen_keeps_all_controls_visible():
    screens = [
        ScreenArea("Built-in", QRect(0, 0, 1440, 900)),
        ScreenArea("External", QRect(-800, 0, 800, 420)),
    ]
    fitted = fit_window_state(
        WindowState(100, -300, 900, 900, "External"),
        screens,
        fallback_size=QSize(440, 580),
        minimum_size=QSize(400, 460),
    )
    assert fitted is not None
    rect, screen = fitted
    assert screen.name == "External"
    assert rect == QRect(-800, 0, 800, 420)
    assert screen.geometry.contains(rect)


@pytest.fixture
def agent():
    return Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="Help.")


def test_chat_dialog_restores_geometry_and_reopen_preserves_user_size(qtbot, agent):
    screen = ScreenArea("Display", QRect(0, 0, 1200, 800))
    with patch("ai_desktop.ui.chat_dialog.ChatDialog._screen_areas", return_value=[screen]), \
            patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        dialog = ChatDialog([agent], agent, ["model"], "model")
        qtbot.addWidget(dialog)
        raw = serialize_window_state(QRect(980, 700, 500, 600), "Display", include_size=True)
        assert dialog.restore_geometry(raw)
        assert dialog.geometry() == QRect(700, 200, 500, 600)
        dialog.show_near(QPoint(1100, 400))
        dialog.resize(460, 540)
        dialog.hide()
        dialog.show_near(QPoint(100, 100))
        assert dialog.size() == QSize(460, 540)
        assert screen.geometry.contains(dialog.geometry())


def test_float_button_restores_and_clamps_position(qtbot):
    screen = ScreenArea("Display", QRect(0, 0, 800, 600))
    with patch("ai_desktop.ui.float_button.FloatButton._screen_areas", return_value=[screen]), \
            patch("ai_desktop.ui.float_button.pin_to_all_spaces"):
        button = FloatButton()
        qtbot.addWidget(button)
        raw = serialize_window_state(QRect(900, 700, 44, 44), "Missing", include_size=False)
        assert button.restore_placement(raw)
        assert button.pos() == QPoint(800 - button.width(), 600 - button.height())


def test_controller_restores_and_debounces_window_state(qtbot, tmp_db, monkeypatch):
    with patch("ai_desktop.main.FloatButton") as float_class, \
            patch("ai_desktop.main.MenuBarIcon") as tray_class:
        float_button = float_class.return_value
        tray = tray_class.return_value
        monkeypatch.setattr(ChatController, "_create_hotkey_backend", lambda _self: MagicMock())
        float_button.placement_state.return_value = '{"float":true}'
        controller = ChatController()
        float_button.restore_placement.assert_called_once_with("")
        controller._schedule_window_state_save()
        controller._schedule_window_state_save()
        qtbot.waitUntil(lambda: controller._window_state_timer.isActive(), timeout=100)
        qtbot.wait(550)
        from ai_desktop.utils.storage import get_setting

        assert get_setting("float_button_placement") == '{"float":true}'
        controller.stop()
        assert controller._stopped
        tray.hide.assert_called()


def test_controller_applies_pet_motion_and_size_preferences(qtbot, tmp_db, monkeypatch):
    monkeypatch.setattr(config, "DESKTOP_PET_ENABLED", True)
    monkeypatch.setattr(config, "DESKTOP_PET_REDUCE_MOTION", False)
    monkeypatch.setattr(config, "DESKTOP_PET_SIZE", "medium")
    monkeypatch.setattr(ChatController, "_create_hotkey_backend", lambda _self: MagicMock())
    with patch("ai_desktop.main.FloatButton") as float_class, \
            patch("ai_desktop.main.MenuBarIcon"):
        controller = ChatController()
        button = float_class.return_value
        float_class.assert_called_once_with(
            pet_enabled=True,
            reduce_motion=False,
            pet_size="medium",
        )

        controller._on_settings_applied({
            "pet_reduce_motion": True,
            "pet_size": "large",
        })
        button.set_reduce_motion.assert_called_once_with(True)
        button.set_pet_size.assert_called_once_with("large")
        controller.stop()
        assert controller._stopped
