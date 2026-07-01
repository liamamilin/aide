"""NSEvent monitor tests."""

import pytest

from ai_desktop.capture.nsevent_monitor import NSEventMonitor, _parse, validate_hotkey


def test_parse_cmd_ctrl_l():
    kc, flags = _parse("<cmd>+<ctrl>+l")
    assert kc == 37  # L
    assert flags & (1 << 20)  # Command
    assert flags & (1 << 18)  # Control


def test_parse_single_key():
    kc, flags = _parse("a")
    assert kc == 0
    assert flags == 0


def test_validate_hotkey_accepts_valid():
    assert validate_hotkey("<cmd>+<ctrl>+l")
    assert validate_hotkey("<shift>+a")


def test_validate_hotkey_rejects_invalid():
    assert not validate_hotkey("")
    assert not validate_hotkey("not-a-hotkey")


def test_register_rejects_invalid_hotkey():
    mon = NSEventMonitor()
    with pytest.raises(ValueError):
        mon.register("not-a-hotkey", lambda: None)


def test_register_sets_key_and_flags():
    mon = NSEventMonitor()
    mon.register("<cmd>+<ctrl>+l", lambda: None)
    assert mon._key_code == 37
    assert mon._mod_flags != 0


def test_set_callback():
    mon = NSEventMonitor()
    cb = lambda: None  # noqa: E731
    mon.set_callback(cb)
    assert mon._callback is cb
