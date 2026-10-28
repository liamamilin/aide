"""Blocking Ollama chat transport for the headless core (no Qt).

One request, one terminal result, same semantics as ``QtChatTransport``: the first
terminal outcome wins, cancel/limit may be called from any thread, and the read
timeout is an inactivity budget rather than a cap on total generation time.
"""
import json
import logging
import socket
import threading
from collections.abc import Callable

import requests

from ai_desktop.llm.chat_client import _exception_error
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, RequestContext, ResultStatus, StreamEvent
from ai_desktop.llm.ndjson import NDJSONDecoder

logger = logging.getLogger(__name__)


def _chunks(response):
    """Yield bytes as they arrive. ``iter_content`` can buffer an un-chunked body until EOF."""
    read1 = getattr(response.raw, "read1", None)  # urllib3 >= 2
    if read1 is None:
        yield from response.iter_content(chunk_size=None)
        return
    while True:
        data = read1(65536, decode_content=True)
        if not data:
            return
        yield data


def _abort(response) -> None:
    """Wake a read blocked in another thread. ``response.close()`` would wait on the reader's
    buffer lock, so shut the socket down instead; the pump thread then closes normally."""
    try:
        sock = response.raw._fp.fp.raw._sock
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        logger.debug("socket shutdown failed", exc_info=True)


class SyncChatTransport:
    def __init__(self, request: RequestContext, *, on_stream: Callable[[StreamEvent], None],
                 allow_tools: bool = False):
        self.request = request
        self._on_stream = on_stream
        self._allow_tools = allow_tools
        self._decoder = NDJSONDecoder(request)
        self._text = ""
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._response = None
        self.result: ChatResult | None = None

    def cancel(self) -> None:
        self._finish(ResultStatus.CANCELLED)

    def limit(self) -> None:
        self._finish(ResultStatus.LIMITED, "任务达到活动时间上限。")

    def run(self, payload: dict) -> ChatResult:
        """Block until a terminal result. The read happens on a pump thread so cancel/limit
        return immediately even if the socket read cannot be interrupted."""
        threading.Thread(target=self._pump, args=(payload,), name="aide-ollama-pump", daemon=True).start()
        self._done.wait()
        return self.result

    def _pump(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
        try:
            with requests.Session() as session:
                response = session.post(f"{self.request.base_url}/api/chat", data=body, headers=headers,
                                        stream=True, allow_redirects=False,
                                        timeout=(self.request.connect_timeout, self.request.timeout))
                with response:
                    self._response = response
                    if self.result is None and response.status_code != 200:
                        self._finish(ResultStatus.FAILED, f"HTTP {response.status_code}", ErrorCode.HTTP)
                    for chunk in _chunks(response):
                        if self.result is not None:
                            break
                        if chunk:
                            self._consume(chunk)
                    if self.result is None:
                        self._consume(b"", final=True)
        except Exception as exc:
            if self.result is None:
                code, error = _exception_error(exc)
                logger.warning("Stream %s failed: %s", self.request.request_id, code.value,
                               exc_info=code == ErrorCode.INTERNAL)
                self._finish(ResultStatus.FAILED, error, code)
        finally:
            self._response = None
            if self.result is None:
                self._finish(ResultStatus.FAILED, "Ollama 连接提前结束，请重试。", ErrorCode.PROTOCOL)

    def _consume(self, data: bytes, *, final: bool = False) -> None:
        try:
            for event in self._decoder.feed(data, final=final):
                if self.result is not None:
                    break
                if event.kind == EventKind.CONTENT:
                    self._text += event.text
                self._on_stream(event)
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

    def _finish(self, status: ResultStatus, error: str = "", code: ErrorCode | None = None) -> None:
        with self._lock:
            if self.result is not None:
                return
            self.result = ChatResult(self.request.request_id, status, self._text, error, code,
                                     self._decoder.assembler.turn, self.request.run_id, self.request.step_id,
                                     self.request.conversation_id)
            response = self._response
        self._done.set()
        if response is not None:
            _abort(response)
