"""Explicit authorization without blocking Qt or retaining secret values."""
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from ai_desktop.services.search_credentials import CredentialError

_active = set()


class CredentialConnectionJob(QThread):
    result = pyqtSignal(str)

    def __init__(self, credentials, provider):
        # No dialog parent: closing settings must not destroy a running QThread.
        super().__init__()
        self.credentials = credentials
        self.provider = provider
        self.cancelled = threading.Event()
        _active.add(self)
        self.finished.connect(self._release)

    def _release(self):
        _active.discard(self)
        self.deleteLater()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            found = self.credentials.connect(self.provider, cancelled=self.cancelled)
            message = (f'{self.provider.title()} 钥匙串已连接，未执行搜索。' if found else
                       f'{self.provider.title()} 尚未保存密钥，请填写后保存。')
        except CredentialError as exc:
            message = str(exc)
        except Exception:
            message = '钥匙串连接失败，请检查应用安装。'
        if not self.cancelled.is_set():
            self.result.emit(message)
