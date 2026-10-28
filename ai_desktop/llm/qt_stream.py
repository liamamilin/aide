"""Incremental Ollama transport. Create and use it in one Qt event-loop thread."""
import logging

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ai_desktop.llm.chat_client import _exception_error
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, RequestContext, ResultStatus
from ai_desktop.llm.ndjson import NDJSONDecoder
from ai_desktop.services.qt_reply import ReplyUnavailableError, call_reply, dispose_reply

logger = logging.getLogger(__name__)


class QtChatTransport(QObject):
    """One request, one terminal result; abort and timers share the reply's thread.

    The connection budget ends on upload progress or response headers. Qt 5 has
    no public socket-connected signal on QNetworkReply, so this includes the
    first request write. Thereafter timeout is an inactivity budget, reset by
    upload/download bytes, independent of total generation duration.
    """

    stream_event = pyqtSignal(object)
    done = pyqtSignal(object)

    def __init__(self, request: RequestContext, *, allow_tools: bool = False):
        super().__init__()
        self.request = request
        self._allow_tools = allow_tools
        self._manager = QNetworkAccessManager(self)
        self._reply = None
        self._decoder = NDJSONDecoder(request)
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
        for signal, slot in (('destroyed', self._reply_destroyed),
                             ('uploadProgress', self._upload_progress),
                             ('metaDataChanged', self._headers_received),
                             ('readyRead', self._ready_read), ('finished', self._finished)):
            self._call_reply(lambda reply: getattr(reply, signal).connect(slot))
            if self.result is not None:
                return
        self._connect_timer.start(max(1, int(self.request.connect_timeout * 1000)))

    @pyqtSlot()
    def cancel(self) -> None:
        self._finish(ResultStatus.CANCELLED)

    @pyqtSlot()
    def limit(self) -> None:
        self._finish(ResultStatus.LIMITED, "任务达到活动时间上限。")

    @pyqtSlot()
    def _connection_timeout(self) -> None:
        self._finish(ResultStatus.FAILED, "连接 Ollama 超时，请检查服务地址。", ErrorCode.TIMEOUT)

    @pyqtSlot()
    def _idle_timeout(self) -> None:
        self._finish(ResultStatus.FAILED, "等待 Ollama 响应超时，请重试。", ErrorCode.TIMEOUT)

    def _activity(self) -> None:
        if self.result is not None:
            return
        self._connected = True
        self._connect_timer.stop()
        self._idle_timer.start(max(1, int(self.request.timeout * 1000)))

    @pyqtSlot("qint64", "qint64")
    def _upload_progress(self, sent: int, total: int) -> None:
        if sent > 0:
            self._activity()

    @pyqtSlot()
    def _reply_destroyed(self) -> None:
        # A reply can disappear before a queued callback or cancellation is
        # delivered. Clear its wrapper before finishing, so cleanup never reads it.
        self._reply = None
        if self.result is None:
            self._finish(ResultStatus.FAILED, "连接意外结束，请重试。", ErrorCode.CONNECTION)

    def _call_reply(self, operation):
        if self.result is not None or not self._started:
            return None
        try:
            reply, value = call_reply(self._manager, self._reply, operation)
            if self.result is None:
                self._reply = reply
            return value
        except ReplyUnavailableError:
            self._reply_destroyed()
            return None

    @pyqtSlot()
    def _headers_received(self) -> None:
        status = self._call_reply(lambda reply: reply.attribute(QNetworkRequest.HttpStatusCodeAttribute))
        if self.result is not None or status is None:
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
                    if event.turn.tool_calls and not self._allow_tools:
                        self._finish(ResultStatus.FAILED, "本次对话未授权工具调用。", ErrorCode.PROTOCOL)
                    else:
                        self._finish(ResultStatus.SUCCEEDED)
                elif event.kind == EventKind.LIMITED:
                    self._finish(ResultStatus.LIMITED, "达到输出上限，回复尚未完成。")
        except Exception as exc:
            code, error = _exception_error(exc)
            logger.warning("Stream %s failed: %s", self.request.request_id, code.value,
                           exc_info=code == ErrorCode.INTERNAL)
            self._finish(ResultStatus.FAILED, error, code)

    @pyqtSlot()
    def _ready_read(self) -> None:
        if self.result is not None or not self._started:
            return
        self._headers_received()
        if self.result is not None:
            return
        data = self._call_reply(lambda reply: bytes(reply.readAll()))
        if self.result is not None:
            return
        if data:
            self._activity()
            self._consume(data)

    @pyqtSlot()
    def _finished(self) -> None:
        if self.result is not None or not self._started:
            return
        self._ready_read()
        if self.result is not None:
            return
        error = self._call_reply(lambda reply: reply.error())
        if self.result is not None:
            return
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
        self.result = ChatResult(self.request.request_id, status, self._text, error, code,
                                 self._decoder.assembler.turn, self.request.run_id, self.request.step_id,
                                 self.request.conversation_id)
        self._connect_timer.stop()
        self._idle_timer.stop()
        # Tear down Python-backed timer connections while we hold the GIL.
        # Leaving them for QObject's worker-thread destructor can invert the
        # Qt connection lock and GIL while the UI updates a widget.
        self._connect_timer.timeout.disconnect(self._connection_timeout)
        self._idle_timer.timeout.disconnect(self._idle_timeout)
        # Drop the reference before abort (which may re-enter finished) and
        # deferred deletion. A late signal can no longer retain a stale reply.
        reply, self._reply = self._reply, None
        dispose_reply(self._manager, reply)
        self.done.emit(self.result)
