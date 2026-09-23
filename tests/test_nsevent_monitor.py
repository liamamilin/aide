"""NSEvent monitor tests."""

import sys
from types import SimpleNamespace

import pytest

from ai_desktop.capture.nsevent_monitor import NSEventMonitor, _parse, validate_hotkey
from ai_desktop.utils.permissions import PermissionStatus


def test_parse_cmd_ctrl_l():
    kc, flags = _parse("<cmd>+<ctrl>+l")
    assert kc == 37  # L
    assert flags & (1 << 20)  # Command
    assert flags & (1 << 18)  # Control


def test_parse_cmd_ctrl_s():
    kc, flags = _parse("<cmd>+<ctrl>+s")
    assert kc == 1  # S
    assert flags & (1 << 20)
    assert flags & (1 << 18)


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


def test_local_monitor_handles_events_from_own_app(monkeypatch):
    """The global monitor excludes our own app; local monitor must bridge that gap."""
    callbacks = {}

    class FakeNSEvent:
        @staticmethod
        def addLocalMonitorForEventsMatchingMask_handler_(mask, handler):
            callbacks["local"] = handler
            return object()

        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            callbacks["global"] = handler
            return object()

        @staticmethod
        def removeMonitor_(monitor):
            return None

    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSEvent=FakeNSEvent))
    monkeypatch.setattr(
        "ai_desktop.utils.permissions.check_all",
        lambda: PermissionStatus(True, True),
    )
    fired = []
    mon = NSEventMonitor()
    mon.register("<cmd>+<ctrl>+s", lambda: fired.append(True))
    mon.start()

    event = SimpleNamespace(
        modifierFlags=lambda: (1 << 20) | (1 << 18),
        keyCode=lambda: 1,
    )
    assert callbacks["local"](event) is event
    assert fired == [True]
    mon.stop()
