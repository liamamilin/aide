"""Wake/permission recovery must not churn healthy native listeners or outlive exit."""
import threading
from unittest.mock import MagicMock

import pytest
from PyQt5.QtCore import Qt

from ai_desktop.capture.hotkey_recovery import HotkeyRecovery
from ai_desktop.utils.permissions import PermissionStatus

GRANTED = PermissionStatus(True, True)
DENIED = PermissionStatus(False, True)


@pytest.fixture
def fixture(qtbot):
    status = [GRANTED]
    observer = MagicMock()
    callback = []
    def subscribe(notify):
        callback.append(notify)
        return observer
    monitors = {'selection': MagicMock(), 'screenshot': MagicMock()}
    for monitor in monitors.values():
        monitor.sync_permissions.return_value = True
    recovery = HotkeyRecovery(monitors, permissions=lambda: status[0], observer_factory=subscribe)
    recovery.start()
    yield recovery, monitors, status, callback, observer
    recovery.stop()


def test_permission_revocation_restores_fast_poll_and_grant_is_reconciled(fixture):
    recovery, monitors, status, _, _ = fixture
    assert recovery._timer.interval() == 60000
    status[0] = DENIED
    recovery.check_now()
    assert recovery._timer.interval() == 3000
    for monitor in monitors.values():
        monitor.sync_permissions.assert_called_with(DENIED, refresh=False)
    status[0] = GRANTED
    recovery.check_now()
    assert recovery._timer.interval() == 60000
    for monitor in monitors.values():
        monitor.sync_permissions.assert_called_with(GRANTED, refresh=False)


def test_healthy_poll_does_not_request_forced_reinstallation(fixture):
    recovery, monitors, _, _, _ = fixture
    for _ in range(3):
        recovery.check_now()
    for monitor in monitors.values():
        assert all(call.kwargs == {'refresh': False} for call in monitor.sync_permissions.call_args_list)


def test_workspace_event_burst_coalesces_on_qt_thread(qtbot, fixture):
    recovery, monitors, _, callback, _ = fixture
    for monitor in monitors.values():
        monitor.sync_permissions.reset_mock()
    restored = []
    recovery.environment_restored.connect(lambda: restored.append(threading.get_ident()))
    thread = threading.Thread(target=lambda: [callback[0]() for _ in range(5)])
    thread.start()
    thread.join(timeout=1)
    qtbot.waitUntil(lambda: bool(restored), timeout=1500)
    assert restored == [threading.get_ident()]
    for monitor in monitors.values():
        monitor.sync_permissions.assert_called_once_with(GRANTED, refresh=True)


def test_stop_cancels_queued_recovery_and_late_notifications(qtbot, fixture):
    recovery, monitors, _, callback, observer = fixture
    callback[0]()
    recovery.stop()
    for monitor in monitors.values():
        monitor.sync_permissions.reset_mock()
    callback[0]()
    recovery.check_now()
    recovery._application_state_changed(Qt.ApplicationActive)
    qtbot.wait(350)
    assert not recovery._timer.isActive() and not recovery._wake_timer.isActive()
    observer.close.assert_called_once()
    for monitor in monitors.values():
        monitor.sync_permissions.assert_not_called()


def test_failed_monitor_does_not_block_other_and_retries_quickly(fixture):
    recovery, monitors, _, _, _ = fixture
    monitors['selection'].sync_permissions.side_effect = RuntimeError('fixture failure')
    monitors['screenshot'].sync_permissions.reset_mock()
    recovery.check_now()
    monitors['screenshot'].sync_permissions.assert_called_once_with(GRANTED, refresh=False)
    assert recovery._timer.interval() == 3000


def test_returned_missing_handle_retries_quickly(fixture):
    recovery, monitors, _, _, _ = fixture
    monitors['selection'].sync_permissions.return_value = False
    recovery.check_now()
    assert recovery._timer.interval() == 3000


def test_inspection_failure_keeps_existing_listeners_and_retries(fixture):
    recovery, monitors, _, _, _ = fixture
    def fail():
        raise RuntimeError('fixture inspection failed')
    recovery._permissions = fail
    for monitor in monitors.values():
        monitor.sync_permissions.reset_mock()
    recovery.check_now()
    assert recovery._timer.interval() == 3000
    for monitor in monitors.values():
        monitor.sync_permissions.assert_not_called()


def test_returning_to_foreground_checks_without_forcing_refresh(fixture):
    recovery, monitors, _, _, _ = fixture
    for monitor in monitors.values():
        monitor.sync_permissions.reset_mock()
    recovery._application_state_changed(Qt.ApplicationInactive)
    recovery._application_state_changed(Qt.ApplicationActive)
    for monitor in monitors.values():
        monitor.sync_permissions.assert_called_once_with(GRANTED, refresh=False)
