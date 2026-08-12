"""SettingsDialog tests — validation, signal emission, cancel behavior."""
from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox

from ai_desktop.config import Agent

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
        "tts_voice": "Serena",
    }
    d = SettingsDialog(current=current)
    qtbot.addWidget(d)
    d.show()
    return d


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
        assert data["tts_voice"] == "Serena"

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

    def test_empty_url_uses_default(self, qtbot, dialog):
        """Empty URL field should use default (not reject)."""
        dialog._widgets["base_url"].setText("")
        # The _on_save method treats empty string as valid (uses default)
        # But it will fail on hotkey validation if hotkey is also empty
        # So set a valid hotkey
        dialog._widgets["hotkey"].setText("<cmd>+<ctrl>+l")
        with qtbot.waitSignal(dialog.settings_applied, timeout=1000):
            dialog._on_save()
