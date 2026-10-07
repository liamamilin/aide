"""Cancellable, bounded search HTTP request in the calling Qt event loop."""
from PyQt5 import sip
from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

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
        self._reply = None
        self._data = bytearray()
        self._manager = QNetworkAccessManager(self)
        # Do not inherit proxy credentials/environment intended for Bash.
        self._manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._finish(SearchResult(
            error="搜索超时，请稍后重试。", error_type="timeout")))

    def start(self):
        if self._reply is not None or self.result is not None:
            return
        req = QNetworkRequest(QUrl(self.request.url))
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setRawHeader(b"x-api-key", self.request.api_key.encode("utf-8"))
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        self._reply = self._manager.post(req, self.request.payload)
        self._reply.setReadBufferSize(65536)
        self._reply.readyRead.connect(self._read)
        self._reply.finished.connect(self._done)
        self._timer.start(self.request.settings.timeout * 1000)

    def cancel(self):
        self._finish(SearchResult(cancelled=True, error_type="cancelled"))

    def limit(self):
        self._finish(SearchResult(error="任务活动时长已用完。", error_type="active_limit"))

    def _read(self):
        if self.result is not None or self._reply is None or sip.isdeleted(self._reply):
            return
        self._data.extend(bytes(self._reply.readAll()))
        if len(self._data) > MAX_RESPONSE_BYTES:
            self._finish(SearchResult(error="搜索响应过大，已停止接收。", error_type="output_limit"))

    def _done(self):
        if self.result is not None:
            return
        if self._reply is None or sip.isdeleted(self._reply):
            self._finish(SearchResult(error='搜索连接已关闭，请重试。', error_type='network'))
            return
        self._read()
        if self.result is not None:
            return
        status = self._reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        self.http_status = status
        if status and status != 200:
            code = int(status)
            kind = {401: "authentication", 403: "authentication", 402: "quota", 429: "rate_limit"}.get(
                code, "redirect" if 300 <= code < 400 else "http")
            result = SearchResult(error=http_error(code), error_type=kind)
        elif self._reply.error() != QNetworkReply.NoError:
            result = SearchResult(error="无法连接搜索服务，请检查网络。", error_type="network")
        else:
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
        # Qt can destroy a reply before a cancellation poll gets here. Detach
        # first so reentrant abort/finished callbacks cannot reuse it, and
        # always notify the waiting event loop even if the native reply is gone.
        reply, self._reply = self._reply, None
        if reply is not None and not sip.isdeleted(reply):
            if not reply.isFinished():
                reply.abort()
            if not sip.isdeleted(reply):
                reply.deleteLater()
        self._data.clear()
        self.finished.emit(result)
