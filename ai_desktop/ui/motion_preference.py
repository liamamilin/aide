"""Bridge the macOS reduced-motion preference to Qt without polling."""
import logging
import sys

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class MotionPreference(QObject):
    changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reduced = False
        self._center = None
        self._observer = None
        app = QApplication.instance()
        if sys.platform != "darwin" or app is None or app.platformName() != "cocoa":
            return
        try:
            import AppKit
            from Foundation import NSOperationQueue
            workspace = AppKit.NSWorkspace.sharedWorkspace()
            self.reduced = bool(workspace.accessibilityDisplayShouldReduceMotion())
            self._center = workspace.notificationCenter()

            def update(_notification):
                reduced = bool(workspace.accessibilityDisplayShouldReduceMotion())
                if reduced != self.reduced:
                    self.reduced = reduced
                    self.changed.emit(reduced)

            self._observer = self._center.addObserverForName_object_queue_usingBlock_(
                AppKit.NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
                None, NSOperationQueue.mainQueue(), update,
            )
            if parent is not None:
                parent.destroyed.connect(self.close)
        except Exception:
            logger.debug("System motion preference is unavailable", exc_info=True)

    def close(self, *_args):
        if self._center is not None and self._observer is not None:
            self._center.removeObserver_(self._observer)
            self._observer = None
