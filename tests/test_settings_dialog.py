"""SettingsDialog tests — validation, signal emission, cancel behavior."""
from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt

from ai_desktop.config import Agent
from ai_desktop.ui.fluent import MessageBox as QMessageBox

AGENTS = [
    Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="..."),
]


@pytest.fixture()
def dialog(qtbot):
    """Create a SettingsDialog with default settings."""
    from ai_desktop.ui.settings_dialog import SettingsDialog
    current = {
        "base_url": "http://localhost:11434",
        "timeout": 120,
        "num_ctx": 4096,
        "num_predict": 256,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "repeat_penalty": 1.1,
        "max_rounds": 10,
        "hotkey": "<cmd>+<ctrl>+l",
        "quick_actions": True,
        "desktop_pet": True,
        "pet_reduce_motion": False,
        "pet_size": "medium",
    }
    d = SettingsDialog(current=current)
    qtbot.addWidget(d)
    d.show()
    return d


@pytest.mark.parametrize("route,field", [
    ("model", "base_url"), ("generation", "temperature"),
    ("search", "search_provider"), ("execution", "execution_workspace"),
    ("desktop", "desktop_pet"),
])
def test_tab_mouse_click_shows_matching_page(qtbot, dialog, route, field):
    # Start elsewhere so every case exercises a real page transition, including
    # returning to the initially selected model tab. Fluent emits itemClicked(True).
    qtbot.mouseClick(dialog._pivot.widget("generation" if route != "generation" else "desktop"), Qt.LeftButton)
    qtbot.mouseClick(dialog._pivot.widget(route), Qt.LeftButton)
    assert dialog._pivot.currentRouteKey() == route
    assert dialog._widgets[field].isVisibleTo(dialog)
    for other_route, _, _, _, start, _ in dialog.GROUPS:
        if other_route != route:
            assert not dialog._widgets[dialog.FIELDS[start][0]].isVisibleTo(dialog)


def test_tab_programmatic_navigation_and_draft_preserved(qtbot, dialog):
    dialog._widgets["base_url"].setText("http://localhost:12345")
    dialog._widgets["temperature"].setValue(0.35)
    with qtbot.assertNotEmitted(dialog.settings_applied):
        dialog._pivot.setCurrentItem("desktop")
        assert dialog._widgets["desktop_pet"].isVisibleTo(dialog)
        dialog._pages.setCurrentIndex(2)
        assert dialog._pivot.currentRouteKey() == "search"
        assert dialog._widgets["search_provider"].isVisibleTo(dialog)
        qtbot.mouseClick(dialog._pivot.widget("model"), Qt.LeftButton)
        assert dialog._widgets["base_url"].text() == "http://localhost:12345"
        qtbot.mouseClick(dialog._pivot.widget("generation"), Qt.LeftButton)
        assert dialog._widgets["temperature"].value() == 0.35


# ── L1: Signal Emission Tests ──────────────────────────

class TestSettingsDialogSignals:
    """Verify settings_applied signal is emitted correctly."""

    def test_valid_settings_emit_signal(self, qtbot, dialog):
        """Filling valid values and clicking save → settings_applied signal with dict."""
        with qtbot.waitSignal(dialog.settings_applied, timeout=1000) as spy:
            # Click the save button (last button in the bottom bar)
            save_btn = None
            for child in dialog.findChildren(object):
                if hasattr(child, 'text') and callable(child.text) and child.text() == "保存":
                    save_btn = child
                    break
            assert save_btn is not None
            qtbot.mouseClick(save_btn, Qt.LeftButton)
        # Signal should carry a dict with all keys
        data = spy.args[0]
        assert isinstance(data, dict)
        assert "base_url" in data
        assert "timeout" in data
        assert "hotkey" in data
        assert data["quick_actions"] is True
        assert data["desktop_pet"] is True
        assert data["pet_reduce_motion"] is False
        assert data["pet_size"] == "medium"

    def test_cancel_does_not_emit(self, qtbot, dialog):
        """Clicking cancel → settings_applied signal is NOT emitted."""
        cancel_btn = None
        for child in dialog.findChildren(object):
            if hasattr(child, 'text') and callable(child.text) and child.text() == "取消":
                cancel_btn = child
                break
        assert cancel_btn is not None
        with qtbot.assertNotEmitted(dialog.settings_applied, wait=500):
            qtbot.mouseClick(cancel_btn, Qt.LeftButton)


# ── L2: Validation Tests ────────────────────────────────

class TestSettingsDialogValidation:
    """Verify input validation rejects invalid data."""

    def test_invalid_url_rejected(self, qtbot, dialog):
        """URL not starting with http:// or https:// → warning, no signal."""
        dialog._widgets["base_url"].setText("ftp://bad.url")
        # Patch QMessageBox.warning to avoid dialog popup
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            with qtbot.assertNotEmitted(dialog.settings_applied, wait=500):
                dialog._on_save()

    def test_invalid_hotkey_rejected(self, qtbot, dialog):
        """Hotkey without proper format → warning, no signal."""
        dialog._widgets["hotkey"].setText("invalid-hotkey")
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            with qtbot.assertNotEmitted(dialog.settings_applied, wait=500):
                dialog._on_save()

    def test_spinbox_ranges(self, qtbot, dialog):
        """SpinBox values should be within defined ranges."""
        # temperature: 0.0 - 2.0
        temp_spin = dialog._widgets["temperature"]
        assert temp_spin.minimum() == 0.0
        assert temp_spin.maximum() == 2.0

        # top_p: 0.0 - 1.0
        top_p_spin = dialog._widgets["top_p"]
        assert top_p_spin.minimum() == 0.0
        assert top_p_spin.maximum() == 1.0

        # timeout: 1 - 600
        timeout_spin = dialog._widgets["timeout"]
        assert timeout_spin.minimum() == 1
        assert timeout_spin.maximum() == 600

        # num_ctx: 256 - 999999
        ctx_spin = dialog._widgets["num_ctx"]
        assert ctx_spin.minimum() == 256

    def test_pet_size_choices_use_stable_values(self, qtbot, dialog):
        size = dialog._widgets["pet_size"]
        assert [size.itemData(index) for index in range(size.count())] == [
            "small", "medium", "large",
        ]
        size.setCurrentIndex(size.findData("large"))
        dialog._widgets["pet_reduce_motion"].setChecked(True)
        with qtbot.waitSignal(dialog.settings_applied, timeout=1000) as spy:
            dialog._on_save()
        assert spy.args[0]["pet_size"] == "large"
        assert spy.args[0]["pet_reduce_motion"] is True

    def test_empty_url_uses_default(self, qtbot, dialog):
        """Empty URL field should use default (not reject)."""
        dialog._widgets["base_url"].setText("")
        # The _on_save method treats empty string as valid (uses default)
        # But it will fail on hotkey validation if hotkey is also empty
        # So set a valid hotkey
        dialog._widgets["hotkey"].setText("<cmd>+<ctrl>+l")
        with qtbot.waitSignal(dialog.settings_applied, timeout=1000):
            dialog._on_save()
