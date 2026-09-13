"""Incremental Ollama transport. Create and use it in one Qt event-loop thread."""
import json
import logging
from collections.abc import Iterator

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ai_desktop.llm.chat_client import StreamProtocolError, _exception_error, _response_message
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, RequestContext, ResultStatus, StreamEvent

logger = logging.getLogger(__name__)


class NDJSONDecoder:
    """Buffer bytes until a whole UTF-8 JSON line is available, including EOF."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self._buffer = bytearray()
        self.terminal = False

    def feed(self, data: bytes, *, final: bool = False) -> Iterator[StreamEvent]:
        if self.terminal:
            return
        self._buffer.extend(data)
        while not self.terminal:
            end = self._buffer.find(b"\n")
            if end < 0:
                if not final or not self._buffer:
                    break
                end = len(self._buffer)
            line = bytes(self._buffer[:end]).strip()
            del self._buffer[:end + 1]
            if not line:
                continue
            value = json.loads(line.decode("utf-8"))
            message, error = _response_message(value)
            if error:
                self.terminal = True
                yield StreamEvent(self.request_id, EventKind.ERROR, error, ErrorCode.SERVER)
                break
            for field, kind in (("thinking", EventKind.THINKING), ("content", EventKind.CONTENT)):
                if message.get(field):
                    yield StreamEvent(self.request_id, kind, message[field])
            if value.get("done"):
                self.terminal = True
                yield StreamEvent(self.request_id, EventKind.COMPLETE)
        if self.terminal:
            self._buffer.clear()
        elif final:
            raise StreamProtocolError("Missing completion event")


class QtChatTransport(QObject):
    """One request, one terminal result; abort and timers share the reply's thread.

    The connection budget ends on upload progress or response headers. Qt 5 has
    no public socket-connected signal on QNetworkReply, so this includes the
    first request write. Thereafter timeout is an inactivity budget, reset by
    upload/download bytes, independent of total generation duration.
    """

    stream_event = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, request: RequestContext):
        super().__init__()
        self.request = request
        self._manager = QNetworkAccessManager(self)
        self._reply = None
        self._decoder = NDJSONDecoder(request.request_id)
        self._text = ""
        self.result = None
        self._started = False
        self._connected = False
        self._connect_timer = QTimer(self)
        self._idle_timer = QTimer(self)
        for timer in (self._connect_timer, self._idle_timer):
            timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._connection_timeout)
        self._idle_timer.timeout.connect(self._idle_timeout)

    def start(self, payload: bytes) -> None:
        if self._started or self.result is not None:
            return
        self._started = True
        request = QNetworkRequest(QUrl(f"{self.request.base_url}/api/chat"))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Accept", b"application/x-ndjson")
        # Preserve POST semantics and report redirects as HTTP errors.
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        self._reply = self._manager.post(request, payload)
        self._reply.uploadProgress.connect(self._upload_progress)
        self._reply.metaDataChanged.connect(self._headers_received)
        self._reply.readyRead.connect(self._ready_read)
        self._reply.finished.connect(self._finished)
        self._connect_timer.start(max(1, int(self.request.connect_timeout * 1000)))

    @pyqtSlot()
    def cancel(self) -> None:
        self._finish(ResultStatus.CANCELLED)

    def _connection_timeout(self) -> None:
        self._finish(ResultStatus.FAILED, "连接 Ollama 超时，请检查服务地址。", ErrorCode.TIMEOUT)

    def _idle_timeout(self) -> None:
        self._finish(ResultStatus.FAILED, "等待 Ollama 响应超时，请重试。", ErrorCode.TIMEOUT)

    def _activity(self) -> None:
        if self.result is not None:
            return
        self._connected = True
        self._connect_timer.stop()
        self._idle_timer.start(max(1, int(self.request.timeout * 1000)))

    def _upload_progress(self, sent: int, total: int) -> None:
        if sent > 0:
            self._activity()

    def _headers_received(self) -> None:
        if self.result is not None:
            return
        status = self._reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status is None:
            return
        self._activity()
        if status != 200:
            self._finish(ResultStatus.FAILED, f"HTTP {status}", ErrorCode.HTTP)

    def _consume(self, data: bytes, *, final: bool = False) -> None:
        try:
            for event in self._decoder.feed(data, final=final):
                if self.result is not None:
                    break
                if event.kind == EventKind.CONTENT:
                    self._text += event.text
                self.stream_event.emit(event)
                if event.kind == EventKind.ERROR:
                    self._finish(ResultStatus.FAILED, event.text, event.error_code)
                elif event.kind == EventKind.COMPLETE:
                    self._finish(ResultStatus.SUCCEEDED)
        except Exception as exc:
            code, error = _exception_error(exc)
            logger.warning("Stream %s failed: %s", self.request.request_id, code.value,
                           exc_info=code == ErrorCode.INTERNAL)
            self._finish(ResultStatus.FAILED, error, code)

    def _ready_read(self) -> None:
        if self.result is not None:
            return
        self._headers_received()
        if self.result is not None:
            return
        data = bytes(self._reply.readAll())
        if data:
            self._activity()
            self._consume(data)

    def _finished(self) -> None:
        if self.result is not None:
            return
        self._ready_read()
        if self.result is not None:
            return
        error = self._reply.error()
        if error == QNetworkReply.NoError:
            self._consume(b"", final=True)
        elif error == QNetworkReply.TimeoutError:
            self._idle_timeout()
        elif error == QNetworkReply.RemoteHostClosedError and self._connected:
            self._finish(ResultStatus.FAILED, "Ollama 连接提前结束，请重试。", ErrorCode.PROTOCOL)
        else:
            self._finish(ResultStatus.FAILED, "无法连接到 Ollama，请检查服务地址。", ErrorCode.CONNECTION)

    def _finish(self, status: ResultStatus, error: str = "", code: ErrorCode | None = None) -> None:
        if self.result is not None:
            return
        # Set the result first: abort can synchronously re-enter finished.
        self.result = ChatResult(self.request.request_id, status, self._text, error, code)
        self._connect_timer.stop()
        self._idle_timer.stop()
        if self._reply is not None:
            if self._reply.isRunning():
                self._reply.abort()
            self._reply.deleteLater()
        self.done.emit(self.result)
