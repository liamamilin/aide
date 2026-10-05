"""A GUI-thread receiver for worker audit events, independent of active chat UI."""
import logging

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from ai_desktop.services import audit_store
from ai_desktop.utils import storage

logger = logging.getLogger(__name__)


class RunAudit(QObject):
    failed = pyqtSignal(str)

    def __init__(self, context, user_message_id, parent=None, *, admission=None):
        super().__init__(parent)
        self.run_id = context.request.run_id
        self.conversation_id = context.request.conversation_id
        self._failed = False
        audit_store.cleanup()
        audit_store.begin_run(context, user_message_id, admission=admission)

    def _failure(self):
        if not self._failed:
            self._failed = True
            logger.warning('Audit write failed run=%s', self.run_id)
            self.failed.emit(self.run_id)

    @pyqtSlot(object)
    def observe(self, event):
        if self._failed or event.run_id != self.run_id or event.conversation_id != self.conversation_id:
            return
        try:
            audit_store.observe(event)
        except Exception:
            self._failure()

    @pyqtSlot(object)
    def complete(self, result):
        if result.run_id != self.run_id or result.conversation_id != self.conversation_id:
            return
        try:
            code = result.error_code.value if result.error_code else ''
            audit_store.finish_run(self.run_id, 'failed' if self._failed else result.status.value,
                                   'audit_write' if self._failed else code)
            audit_store.cleanup()
        except Exception:
            self._failure()

    def cancelling(self):
        try:
            with storage._conn() as db:
                db.execute('UPDATE agent_runs SET status="cancelling" WHERE run_id=? AND ended_at IS NULL',
                           (self.run_id,))
        except Exception:
            self._failure()
