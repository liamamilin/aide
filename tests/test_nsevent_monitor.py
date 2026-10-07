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


@pytest.mark.parametrize('key,code', [('<space>', 49), ('<f1>', 122), ('<left>', 123),
                                     ('a', 0), ('7', 26), ('8', 28), ('9', 25), ('=', 24)])
def test_named_and_zero_code_keys_parse_consistently(key, code):
    assert _parse('<cmd>+<ctrl>+'+key) == (code, (1 << 20) | (1 << 18))


@pytest.mark.parametrize('hotkey', ['<cmd>+<unknown>+l', '<cmd>+l+x', '<cmd>+', '<cmd>++l',
                                    '<cmd>+unsupported', '<cmd>+<ctrl>', '<cmd>+<command>+l', None])
def test_rejects_shortcuts_the_native_backend_cannot_represent(hotkey):
    assert not validate_hotkey(hotkey)


def monitor_fixture(monkeypatch):
    handlers = {}
    class Events:
        @staticmethod
        def addLocalMonitorForEventsMatchingMask_handler_(mask, handler):
            handlers['local'] = handler
            return object()
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            handlers['global'] = handler
            return object()
        @staticmethod
        def removeMonitor_(monitor):
            pass
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(NSEvent=Events))
    monkeypatch.setattr('ai_desktop.utils.permissions.check_all', lambda: PermissionStatus(True, True))
    fired = []
    monitor = NSEventMonitor()
    monitor.register('<cmd>+<ctrl>+l', lambda: fired.append('old'))
    monitor.start()
    return monitor, handlers, fired


def event(*, repeat=False, extra=0, code=37):
    return SimpleNamespace(modifierFlags=lambda: (1 << 20) | (1 << 18) | extra,
                           keyCode=lambda: code, isARepeat=lambda: repeat)


@pytest.mark.parametrize('scope', ['local', 'global'])
def test_long_press_does_not_retrigger_and_extra_modifiers_do_not_match(monkeypatch, scope):
    monitor, handlers, fired = monitor_fixture(monkeypatch)
    handler = handlers[scope]
    handler(event())
    handler(event(repeat=True))
    handler(event(extra=1 << 17))
    handler(event(extra=1 << 19))
    handler(event(extra=1 << 16))  # Caps Lock does not change the shortcut.
    assert fired == ['old', 'old']
    monitor.stop()


def test_retired_native_handler_cannot_invoke_replaced_callback(monkeypatch):
    monitor, handlers, fired = monitor_fixture(monkeypatch)
    previous = handlers['local']
    monitor.reregister('<cmd>+<ctrl>+s', lambda: fired.append('new'))
    previous(event())
    handlers['local'](event(code=1))
    current = handlers['local']
    monitor.stop()
    current(event(code=1))
    assert fired == ['new']


def test_local_only_install_never_checks_permissions_or_registers_global(monkeypatch):
    monitor, handlers, fired = monitor_fixture(monkeypatch)
    monitor.stop()
    handlers.clear()
    def forbidden():
        raise AssertionError('Local fixture must not query permissions')
    monkeypatch.setattr('ai_desktop.utils.permissions.check_all', forbidden)
    monitor.start(local_only=True)
    assert set(handlers) == {'local'}
    assert monitor._monitor is None
    handlers['local'](event())
    assert fired == ['old']
    monitor.stop()


def test_permission_loss_and_restore_keep_local_handler_alive(monkeypatch):
    monitor, handlers, fired = monitor_fixture(monkeypatch)
    local_handle, local_callback = monitor._local_monitor, handlers['local']
    retired = handlers['global']
    monitor.sync_permissions(PermissionStatus(False, True))
    assert monitor._monitor is None and monitor._local_monitor is local_handle
    retired(event())
    local_callback(event())
    assert fired == ['old']
    monitor.sync_permissions(PermissionStatus(True, True))
    assert monitor._monitor is not None and monitor._local_monitor is local_handle
    assert handlers['local'] is local_callback
    handlers['global'](event())
    assert fired == ['old', 'old']
    monitor.stop()


def test_healthy_permission_check_does_not_replace_native_handles(monkeypatch):
    monitor, handlers, _ = monitor_fixture(monkeypatch)
    handles = (monitor._local_monitor, monitor._monitor)
    callbacks = dict(handlers)
    for _ in range(5):
        assert monitor.sync_permissions(PermissionStatus(True, True))
    assert (monitor._local_monitor, monitor._monitor) == handles
    assert handlers == callbacks
    monitor.stop()


def test_wake_refresh_replaces_only_global_and_invalidates_old_global_callback(monkeypatch):
    monitor, handlers, fired = monitor_fixture(monkeypatch)
    local = monitor._local_monitor
    old_global, old_handle = handlers['global'], monitor._monitor
    assert monitor.sync_permissions(PermissionStatus(True, True), refresh=True)
    assert monitor._monitor is not old_handle and monitor._local_monitor is local
    old_global(event())
    handlers['local'](event())
    handlers['global'](event())
    assert fired == ['old', 'old']
    monitor.stop()
