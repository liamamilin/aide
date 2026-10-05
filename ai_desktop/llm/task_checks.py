"""Fresh asynchronous selected-model identity/capability checks for tool runs."""
import json

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from ai_desktop.services.task_admission import TASK_MODEL, TaskModelSettings, local_service_url, validate_discovery


class TaskChecks(QObject):
    completed = pyqtSignal(int, object, str)

    def __init__(self, parent=None, *, timeout_ms=5000):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._finish(None, '模型准入检查超时，请检查本机 Ollama。'))
        self._timeout_ms = timeout_ms
        self._sequence = 0
        self._active = False
        self._reply = None
        self._data = bytearray()
        self._values = []

    def check(self, base_url, model=TASK_MODEL, *, settings=None):
        self.cancel()
        self._sequence += 1
        self._active = True
        self._values = []
        self._model = model
        self._settings = settings or TaskModelSettings.from_config()
        try:
            self._base_url = local_service_url(base_url)
        except ValueError as exc:
            sequence = self._sequence
            error = str(exc)
            QTimer.singleShot(0, lambda: self._invalid(sequence, error))
            return sequence
        self._timer.start(self._timeout_ms)
        self._next()
        return self._sequence

    def _invalid(self, sequence, error):
        if self._active and sequence == self._sequence:
            self._finish(None, error)

    def cancel(self):
        self._active = False
        self._timer.stop()
        self._release_reply()
        self._data.clear()
        self._values = []

    def _release_reply(self):
        reply, self._reply = self._reply, None
        if reply is not None:
            if reply.isRunning():
                reply.abort()
            reply.deleteLater()

    def _next(self):
        paths = ('/api/version', '/api/tags', '/api/show')
        request = QNetworkRequest(QUrl(self._base_url + paths[len(self._values)]))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        if len(self._values) == 2:
            reply = self._manager.post(request, json.dumps({'model': self._model}).encode())
        else:
            reply = self._manager.get(request)
        self._reply = reply
        reply.setReadBufferSize(65536)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._done(reply))

    def _read(self, reply):
        if not self._active or self._reply is not reply:
            return
        self._data.extend(bytes(reply.readAll()))
        if len(self._data) > 1024 * 1024:
            self._finish(None, '模型准入响应过大，检查已停止。')

    def _done(self, reply):
        if not self._active or self._reply is not reply:
            return
        self._read(reply)
        if not self._active:
            return
        if reply.error() != QNetworkReply.NoError or reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) != 200:
            self._finish(None, '无法验证本机 Ollama，请检查服务连接。')
            return
        try:
            value = json.loads(self._data.decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            self._finish(None, '模型准入响应无效，未启用工具。')
            return
        self._values.append(value)
        self._data.clear()
        self._release_reply()
        if len(self._values) < 3:
            self._next()
            return
        try:
            result = validate_discovery(self._base_url, *self._values, model=self._model, settings=self._settings)
        except ValueError as exc:
            self._finish(None, str(exc))
        else:
            self._finish(result, '')

    def _finish(self, admission, error):
        if not self._active:
            return
        sequence = self._sequence
        self.cancel()
        self.completed.emit(sequence, admission, error)
