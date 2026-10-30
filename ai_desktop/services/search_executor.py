"""Run-owned web_search execution, using the worker's Qt event loop.

Settings are immutable; credentials are read lazily once and never leave this
executor. Search results are data, not instructions. No retries or fallback.
"""
import time

from PyQt5.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from ai_desktop.services.qt_search import SearchJob
from ai_desktop.services.search_record import SearchRecorder
from ai_desktop.services.web_search import SearchError, SearchResult, build_request


class SearchExecutor(SearchRecorder):
    def __call__(self, args, context):
        started = time.monotonic()
        interrupted = self._interrupted(context)
        if interrupted is not None:
            return self._output(interrupted, started)
        self._load_key(context)
        interrupted = self._interrupted(context)
        if interrupted is not None or self._credential_error is not None:
            return self._output(interrupted or self._credential_error, started)
        try:
            request = build_request(self.settings, self._key, args['query'], args.get('objective', ''))
        except (SearchError, KeyError):
            return self._output(SearchResult(error='搜索参数或密钥无效。', error_type='invalid_request'), started)
        loop = QEventLoop()
        job = SearchJob(request)
        poll = QTimer()
        poll.setInterval(20)

        def check():
            result = self._interrupted(context)
            if result is not None:
                job.cancel() if result.cancelled else job.limit()

        poll.timeout.connect(check)
        job.finished.connect(loop.quit)
        try:
            check()
            if job.result is None:
                if context.on_state:
                    context.on_state('searching')
                job.start()
                poll.start()
                if job.result is None:
                    loop.exec_()
            result = self._interrupted(context) or job.result
            return self._output(result, started)
        finally:
            poll.stop()
            poll.timeout.disconnect(check)
            job.cancel()
            job.finished.disconnect(loop.quit)
            poll.deleteLater()
            job.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
