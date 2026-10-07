"""Native notification/visibility fixture; never sleeps, changes TCC, or listens globally."""
import sys


def acceptance(ctl, wait):
    from AppKit import (
        NSWorkspace,
        NSWorkspaceDidWakeNotification,
        NSWorkspaceSessionDidBecomeActiveNotification,
    )
    from PyQt5.QtWidgets import QApplication

    from ai_desktop.capture.hotkey_recovery import HotkeyRecovery
    from ai_desktop.utils.permissions import PermissionStatus

    entry = sys.modules[type(ctl).__module__]
    original_factory = entry.HotkeyRecovery
    def factory(monitors, parent):
        return HotkeyRecovery(monitors, parent, permissions=lambda: PermissionStatus(False, False))

    try:
        entry.HotkeyRecovery = factory
        ctl.start()
    finally:
        entry.HotkeyRecovery = original_factory
    recovery = ctl._hotkey_recovery
    observer = recovery._observer
    assert observer is not None and len(observer._tokens) == 2
    local_handles = (ctl.hotkey._local_monitor, ctl.hotkey_img._local_monitor)
    assert all(handle is not None for handle in local_handles)
    assert ctl.hotkey._monitor is None and ctl.hotkey_img._monitor is None
    recovered = []
    recovery.environment_restored.connect(lambda: recovered.append(True))
    workspace = NSWorkspace.sharedWorkspace()
    center = workspace.notificationCenter()
    dialog = ctl._dialog
    screen = QApplication.primaryScreen().availableGeometry()
    ctl._hide_float_entry()
    dialog.move(screen.right()+10000, screen.bottom()+10000)
    ctl.float_btn.move(screen.right()+10000, screen.bottom()+10000)

    def notify():
        center.postNotificationName_object_(NSWorkspaceDidWakeNotification, workspace)
        center.postNotificationName_object_(NSWorkspaceSessionDidBecomeActiveNotification, workspace)

    try:
        notify()
        wait(lambda: len(recovered) == 1)
        wait(lambda: not ctl._screen_recovery_timer.isActive())
        assert (ctl.hotkey._local_monitor, ctl.hotkey_img._local_monitor) == local_handles
        assert ctl.hotkey._monitor is None and ctl.hotkey_img._monitor is None
        assert ctl.float_btn.isHidden()
        areas = [item.availableGeometry() for item in QApplication.screens()]
        assert any(area.contains(dialog.geometry()) for area in areas)
        assert any(area.contains(ctl.float_btn.geometry()) for area in areas)
        late_callback = recovery._environment_event.emit
        ctl.stop()
        wait(lambda: ctl._stopped)
        late_callback()
        notify()
        QApplication.processEvents()
        assert observer._closed and observer._tokens == []
        assert recovery._observer is None
        assert not recovery._timer.isActive() and not recovery._wake_timer.isActive()
        assert ctl.hotkey._local_monitor is None and ctl.hotkey_img._local_monitor is None
        assert recovered == [True]
        return {'native_workspace_observers': 2, 'synthetic_workspace_notifications': True,
                'wake_and_session_events_coalesced': True, 'local_handles_preserved': True,
                'hidden_pet_stays_hidden': True, 'offscreen_windows_recovered': True,
                'shutdown_removes_observers_and_timers': True, 'late_notification_ignored': True,
                'system_permissions_changed': False, 'user_clipboard_accessed': False,
                'global_listener_started': False, 'physical_sleep_wake_verified': False,
                'physical_multidisplay_verified': False}
    finally:
        recovery.stop()
