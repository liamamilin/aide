"""Run-owned web_search execution, using the worker's Qt event loop.

Settings are immutable; credentials are read lazily once and never leave this
executor. Search results are data, not instructions. No retries or fallback.
"""
import json
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone

from PyQt5.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from ai_desktop.llm.run_types import ToolOutput
from ai_desktop.services.qt_search import SearchJob
from ai_desktop.services.search_credentials import SearchCredentials
from ai_desktop.services.web_search import SearchError, SearchResult, SearchSettings, build_request


class SearchExecutor:
    def __init__(self, settings: SearchSettings, *, credentials=None, result_bytes=4096):
        if not isinstance(settings, SearchSettings) or not 512 <= result_bytes <= 4096:
            raise ValueError('Invalid search executor settings')
        self.settings = settings
        self._credentials = credentials if credentials is not None else SearchCredentials()
        self._key = None
        self._credential_error = None
        self._next_source = 1
        self._sources = {}
        self._result_bytes = result_bytes

    @property
    def sources(self):
        return dict(self._sources)

    @staticmethod
    def _interrupted(context):
        if context.cancelled.is_set():
            return SearchResult(cancelled=True, error_type='cancelled')
        if context.active_deadline is not None and time.monotonic() >= context.active_deadline:
            return SearchResult(error='任务活动时长已用完。', error_type='active_limit')
        return None

    def __call__(self, args, context):
        started = time.monotonic()
        interrupted = self._interrupted(context)
        if interrupted is not None:
            return self._output(interrupted, started)
        if self._key is None and self._credential_error is None:
            try:
                self._key = self._credentials.get(self.settings.provider, interactive=False)
                if not self._key:
                    self._credential_error = SearchResult(
                        error='请在设置的联网搜索页面保存 API 密钥。', error_type='missing_credentials')
            except Exception:
                # Neither native diagnostics nor credential values reach logs/model/UI.
                self._credential_error = SearchResult(
                    error='无法读取搜索密钥，请在设置中重新保存并允许钥匙串访问。',
                    error_type='credentials_unavailable')
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
            job.cancel()
            job.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def _output(self, result, started):
        sources = [asdict(replace(source, source_id=f'S{self._next_source + i}'))
                   for i, source in enumerate(result.sources)]
        record = {'provider': self.settings.provider,
                  'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                  'duration': round(time.monotonic() - started, 3), 'truncated': False,
                  'returned_sources': len(sources), 'omitted_sources': 0,
                  'error_type': result.error_type, 'error': result.error, 'sources': sources}

        def encode():
            record['omitted_sources'] = record['returned_sources'] - len(sources)
            return json.dumps(record, ensure_ascii=False, separators=(',', ':'))

        # Keep valid JSON and exact URLs within the model's 4 KiB tool budget.
        # Shrink all excerpts fairly before dropping complete sources; never cut a URL.
        text = encode()
        while len(text.encode('utf-8')) > self._result_bytes and sources:
            record['truncated'] = True
            if any(source['excerpt'] for source in sources):
                for source in sources:
                    source['excerpt'] = source['excerpt'][:len(source['excerpt']) // 2]
            elif any(len(source['title']) > 80 for source in sources):
                for source in sources:
                    source['title'] = source['title'][:80]
            else:
                sources.pop()
            text = encode()
        for source in sources:
            self._sources[source['source_id']] = dict(source)
        self._next_source += len(sources)
        return ToolOutput(text, not result.ok)
