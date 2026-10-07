"""Permission reconciliation and event-driven macOS hotkey recovery on Qt's thread."""
import logging
import sys

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

from ai_desktop.utils.permissions import check_all

logger = logging.getLogger(__name__)


class WorkspaceRecoveryObserver:
    """Observe only wake/session activation; no keyboard listener or power change."""

    def __init__(self, callback):
        self._center = None
        self._tokens = []
        self._closed = False
        app = QApplication.instance()
        if sys.platform != 'darwin' or app is None or app.platformName() != 'cocoa':
            return
        try:
            from AppKit import (
                NSWorkspace,
                NSWorkspaceDidWakeNotification,
                NSWorkspaceSessionDidBecomeActiveNotification,
            )
            from Foundation import NSOperationQueue

            self._center = NSWorkspace.sharedWorkspace().notificationCenter()
            def received(_notification):
                if not self._closed:
                    callback()
            for name in (NSWorkspaceDidWakeNotification, NSWorkspaceSessionDidBecomeActiveNotification):
                token = self._center.addObserverForName_object_queue_usingBlock_(
                    name, None, NSOperationQueue.mainQueue(), received)
                if token is None:
                    raise RuntimeError('Workspace observer installation failed')
                self._tokens.append(token)
        except Exception:
            self.close()
            logger.warning('Workspace recovery notifications unavailable', exc_info=True)

    def close(self):
        self._closed = True
        for token in self._tokens:
            try:
                self._center.removeObserver_(token)
            except Exception:
                logger.warning('Workspace observer removal failed', exc_info=True)
        self._tokens.clear()


class HotkeyRecovery(QObject):
    """Reconcile missing handles; refresh global listeners only after a system event.

    Checks are silent and never request permissions. Healthy polling keeps native
    handles intact; missing permission or a failed install retries after 3s.
    """

    environment_restored = pyqtSignal()
    _environment_event = pyqtSignal()

    def __init__(self, monitors, parent=None, *, permissions=None, observer_factory=None):
        super().__init__(parent)
        self._monitors = dict(monitors)
        self._permissions = permissions or check_all
        self._observer_factory = observer_factory or WorkspaceRecoveryObserver
        self._observer = None
        self._active = False
        self._status = None
        self._app = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_now)
        self._wake_timer = QTimer(self)
        self._wake_timer.setSingleShot(True)
        self._wake_timer.setInterval(250)
        self._wake_timer.timeout.connect(self._recover_environment)
        self._environment_event.connect(self._schedule_recovery)

    def start(self):
        if self._active:
            return
        self._active = True
        try:
            self._observer = self._observer_factory(self._environment_event.emit)
        except Exception:
            logger.warning('Cannot subscribe to workspace recovery', exc_info=True)
        self._app = QApplication.instance()
        if self._app is not None:
            self._app.applicationStateChanged.connect(self._application_state_changed)
        self.check_now()

    def stop(self):
        self._active = False
        self._timer.stop()
        self._wake_timer.stop()
        if self._observer is not None:
            self._observer.close()
            self._observer = None
        if self._app is not None:
            self._app.applicationStateChanged.disconnect(self._application_state_changed)
            self._app = None

    def check_now(self, *, refresh=False):
        if not self._active:
            return
        try:
            status = self._permissions()
            if status != self._status:
                logger.info('Hotkey permissions: accessibility=%s input_monitoring=%s', *status)
                self._status = status
            ready = True
            for name, monitor in self._monitors.items():
                try:
                    ready = monitor.sync_permissions(status, refresh=refresh) and ready
                except Exception:
                    ready = False
                    logger.warning('Hotkey recovery failed: %s', name, exc_info=True)
            self._timer.start(60000 if status.all_granted and ready else 3000)
        except Exception:
            logger.warning('Hotkey permission inspection failed; retaining listeners', exc_info=True)
            self._timer.start(3000)

    def _application_state_changed(self, state):
        if state == Qt.ApplicationActive:
            self.check_now()

    def _schedule_recovery(self):
        if self._active:
            self._wake_timer.start()

    def _recover_environment(self):
        if self._active:
            self.check_now(refresh=True)
            self.environment_restored.emit()
