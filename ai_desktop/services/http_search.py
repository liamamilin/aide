"""web_search for the headless core: same request/response rules as the Qt job, over ``requests``.

The request runs on a helper thread so a stop or the run's time budget is honoured within ~50 ms.
No redirects, no proxy environment, the response is read in bounded chunks. No retries or fallback.
"""
import threading
import time

import requests

from ai_desktop.services.search_record import SearchRecorder
from ai_desktop.services.web_search import (
    MAX_RESPONSE_BYTES,
    SearchError,
    SearchResult,
    build_request,
    http_error,
    parse_response,
)


class HttpSearchExecutor(SearchRecorder):
    def __call__(self, args, context):
        started = time.monotonic()
        interrupted = self._interrupted(context)
        if interrupted is None:
            self._load_key(context)
            interrupted = self._interrupted(context) or self._credential_error
        if interrupted is not None:
            return self._output(interrupted, started)
        try:
            request = build_request(self.settings, self._key, args['query'], args.get('objective', ''))
        except (SearchError, KeyError):
            return self._output(SearchResult(error='搜索参数或密钥无效。', error_type='invalid_request'), started)
        if context.on_state:
            context.on_state('searching')
        box: dict = {}
        worker = threading.Thread(target=self._fetch, args=(request, box), daemon=True)
        worker.start()
        deadline = started + self.settings.timeout
        while worker.is_alive():
            worker.join(0.05)
            stop = self._interrupted(context)
            if stop is not None:
                box['abandoned'] = True
                return self._output(stop, started)
            if time.monotonic() >= deadline:
                box['abandoned'] = True
                return self._output(SearchResult(error='搜索超时，请稍后重试。', error_type='timeout'), started)
        return self._output(box.get('result') or SearchResult(error='搜索失败。', error_type='network'), started)

    @staticmethod
    def _fetch(request, box):
        try:
            with requests.Session() as session:
                session.trust_env = False            # never inherit proxy credentials meant for Bash
                with session.post(request.url, data=request.payload, allow_redirects=False, stream=True,
                                  timeout=(10, request.settings.timeout),
                                  headers={"Content-Type": "application/json", "x-api-key": request.api_key}) as r:
                    if r.status_code != 200:
                        box['result'] = SearchResult(error=http_error(r.status_code), error_type='http')
                        return
                    data = bytearray()
                    for chunk in r.iter_content(65536):
                        data += chunk
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise SearchError("搜索响应过大，已停止接收。")
            box['result'] = parse_response(request.settings, bytes(data))
        except SearchError as exc:
            box['result'] = SearchResult(error=str(exc), error_type='response')
        except requests.RequestException:
            box['result'] = SearchResult(error='无法连接搜索服务，请检查网络。', error_type='network')
