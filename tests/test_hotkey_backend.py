"""Platform selection tests for the global hotkey backend."""

from unittest.mock import MagicMock, patch

from ai_desktop import config
from ai_desktop.main import _create_hotkey_backend


def test_macos_always_uses_nsevent_backend():
    callback = MagicMock()
    backend = MagicMock()

    with patch("ai_desktop.main.sys.platform", "darwin"), patch(
        "ai_desktop.capture.nsevent_monitor.NSEventMonitor", return_value=backend
    ) as backend_class, patch(
        "ai_desktop.capture.hotkey_listener.HotkeyListener"
    ) as pynput_class:
        result = _create_hotkey_backend(callback)

    assert result is backend
    backend_class.assert_called_once_with()
    pynput_class.assert_not_called()
    backend.register.assert_called_once_with(config.HOTKEY, callback)


def test_non_macos_uses_pynput_backend():
    callback = MagicMock()
    backend = MagicMock()

    with patch("ai_desktop.main.sys.platform", "linux"), patch(
        "ai_desktop.capture.hotkey_listener.HotkeyListener", return_value=backend
    ) as backend_class, patch(
        "ai_desktop.capture.nsevent_monitor.NSEventMonitor"
    ) as nsevent_class:
        result = _create_hotkey_backend(callback)

    assert result is backend
    backend_class.assert_called_once_with()
    nsevent_class.assert_not_called()
    backend.register.assert_called_once_with(config.HOTKEY, callback)
