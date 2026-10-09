"""Cancellable, bounded search HTTP request in the calling Qt event loop."""
from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from ai_desktop.services.qt_reply import ReplyUnavailableError, call_reply, dispose_reply
from ai_desktop.services.web_search import (
    MAX_RESPONSE_BYTES,
    SearchError,
    SearchRequest,
    SearchResult,
    http_error,
    parse_response,
)


class SearchJob(QObject):
    finished = pyqtSignal(object)

    def __init__(self, request: SearchRequest, parent=None):
        super().__init__(parent)
        self.request = request
        self.result = None
        self.http_status = None
        self._started = False
        self._reply = None
        self._data = bytearray()
        self._manager = QNetworkAccessManager(self)
        # Do not inherit proxy credentials/environment intended for Bash.
        self._manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)

    def start(self):
        if self._started or self.result is not None:
            return
        self._started = True
        req = QNetworkRequest(QUrl(self.request.url))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setRawHeader(b"x-api-key", self.request.api_key.encode("utf-8"))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        self._reply = self._manager.post(req, self.request.payload)
        self._call_reply(lambda reply: reply.setReadBufferSize(65536))
        for signal, slot in (('destroyed', self._reply_destroyed),
                             ('readyRead', self._read), ('finished', self._done)):
            self._call_reply(lambda reply: getattr(reply, signal).connect(slot))
            if self.result is not None:
                return
        self._timer.start(self.request.settings.timeout * 1000)

    @pyqtSlot()
    def _reply_destroyed(self):
        self._reply = None
        if self.result is None:
            self._finish(SearchResult(error='搜索连接已关闭，请重试。', error_type='network'))

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
    def cancel(self):
        self._finish(SearchResult(cancelled=True, error_type="cancelled"))

    @pyqtSlot()
    def limit(self):
        self._finish(SearchResult(error="任务活动时长已用完。", error_type="active_limit"))

    @pyqtSlot()
    def _timeout(self):
        self._finish(SearchResult(error="搜索超时，请稍后重试。", error_type="timeout"))

    @pyqtSlot()
    def _read(self):
        if self.result is not None or not self._started:
            return
        data = self._call_reply(lambda reply: bytes(reply.readAll()))
        if self.result is not None:
            return
        self._data.extend(data)
        if len(self._data) > MAX_RESPONSE_BYTES:
            self._finish(SearchResult(error="搜索响应过大，已停止接收。", error_type="output_limit"))

    @pyqtSlot()
    def _done(self):
        if self.result is not None or not self._started:
            return
        self._read()
        if self.result is not None:
            return
        status = self._call_reply(lambda reply: reply.attribute(QNetworkRequest.HttpStatusCodeAttribute))
        if self.result is not None:
            return
        self.http_status = status
        if status and status != 200:
            code = int(status)
            kind = {401: "authentication", 403: "authentication", 402: "quota", 429: "rate_limit"}.get(
                code, "redirect" if 300 <= code < 400 else "http")
            result = SearchResult(error=http_error(code), error_type=kind)
        else:
            error = self._call_reply(lambda reply: reply.error())
            if self.result is not None:
                return
            if error != QNetworkReply.NoError:
                self._finish(SearchResult(error="无法连接搜索服务，请检查网络。", error_type="network"))
                return
            try:
                result = parse_response(self.request.settings, bytes(self._data))
            except SearchError as exc:
                result = SearchResult(error=str(exc), error_type="protocol")
        self._finish(result)

    def _finish(self, result):
        if self.result is not None:
            return
        self.result = result
        self._timer.stop()
        # Disconnect before deferred destruction in the worker thread, so
        # QObject cleanup never waits for the GIL with a Qt connection lock.
        self._timer.timeout.disconnect(self._timeout)
        # Qt can destroy a reply before a cancellation poll gets here. Detach
        # first so reentrant abort/finished callbacks cannot reuse it, and
        # always notify the waiting event loop even if the native reply is gone.
        reply, self._reply = self._reply, None
        dispose_reply(self._manager, reply)
        self._data.clear()
        self.finished.emit(result)
