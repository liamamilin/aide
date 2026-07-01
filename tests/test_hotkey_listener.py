"""Hotkey listener tests."""

import pytest

from ai_desktop.capture.hotkey_listener import HotkeyListener, validate_hotkey


def test_validate_hotkey_accepts_pynput_format():
    assert validate_hotkey("<cmd>+<ctrl>+l")


def test_validate_hotkey_rejects_invalid_format():
    assert not validate_hotkey("not-a-hotkey")


def test_register_rejects_invalid_hotkey():
    listener = HotkeyListener()
    with pytest.raises(ValueError):
        listener.register("not-a-hotkey", lambda: None)
