"""Background preparation and an interruptible Qt network event loop."""
import json
import logging
import threading

from PyQt5.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, Qt, QThread, pyqtSignal

from ai_desktop.llm.chat_client import ChatClient, _exception_error, _payload
from ai_desktop.llm.events import ChatResult, EventKind, ResultStatus
from ai_desktop.llm.qt_stream import QtChatTransport
from ai_desktop.utils import storage
from ai_desktop.utils.storage import Message

logger = logging.getLogger(__name__)


class StreamingChatWorker(QThread):
    thinking_event = pyqtSignal(object)
    content_event = pyqtSignal(object)
    thinking_chunk = pyqtSignal(str)
    chunk = pyqtSignal(str)
    done = pyqtSignal(object)
    _cancel_requested = pyqtSignal()

    def __init__(self, messages: list[Message], system_prompt: str, model: str = "", parent: QObject | None = None,
                 *, conversation_id: int = 0, agent_id: str = ""):
        super().__init__(parent)
        self.request = ChatClient(model=model).create_request(
            messages, system_prompt, conversation_id=conversation_id, agent_id=agent_id,
        )
        # Unlike QThread interruption, this also remembers cancellation before start().
        self._cancelled = threading.Event()
        self._release_lock = threading.Lock()
        self._attachments_released = False
        self._retained_attachments = storage.retain_attachment_paths(
            [path for message in self.request.messages for path in message.images]
        )

    def cancel(self) -> None:
        self._cancelled.set()
        super().requestInterruption()
        self._cancel_requested.emit()

    def requestInterruption(self) -> None:
        self.cancel()

    def release_attachments(self) -> None:
        """Release request-owned files once, including setup-failure paths."""
        with self._release_lock:
            if self._attachments_released:
                return
            self._attachments_released = True
        storage.release_attachment_paths(self._retained_attachments)

    def run(self) -> None:
        transport = None
        result = ChatResult(self.request.request_id, ResultStatus.CANCELLED)
        try:
            if not self._cancelled.is_set():
                # Includes file I/O and base64 encoding; never runs on the UI thread.
                payload = json.dumps(_payload(self.request, stream=True)).encode("utf-8")
                if not self._cancelled.is_set():
                    loop = QEventLoop()
                    transport = QtChatTransport(self.request)
                    transport.stream_event.connect(self._forward_event, Qt.DirectConnection)
                    transport.done.connect(loop.quit)
                    self._cancel_requested.connect(transport.cancel, Qt.QueuedConnection)
                    # Check again after connecting to close the cancellation setup race.
                    if self._cancelled.is_set():
                        transport.cancel()
                    else:
                        transport.start(payload)
                    if transport.result is None:
                        loop.exec_()
                    if transport.result is None:
                        transport.cancel()  # An externally stopped loop is still a terminal cancellation.
                    result = transport.result
        except Exception as exc:
            code, error = _exception_error(exc)
            logger.warning("Worker %s failed: %s", self.request.request_id, code.value,
                           exc_info=code.value == "internal")
            if not self._cancelled.is_set():
                result = ChatResult(self.request.request_id, ResultStatus.FAILED, error=error, error_code=code)
        finally:
            if transport is not None:
                self._cancel_requested.disconnect(transport.cancel)
                transport.cancel()  # Idempotent; also releases a reply after an unexpected exception.
                transport.deleteLater()
                # Deferred deletes must run before this thread's event loop disappears.
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.release_attachments()
        self.done.emit(result)

    def _forward_event(self, event) -> None:
        if event.kind == EventKind.THINKING:
            self.thinking_event.emit(event)
            self.thinking_chunk.emit(event.text)
        elif event.kind == EventKind.CONTENT:
            self.content_event.emit(event)
            self.chunk.emit(event.text)
