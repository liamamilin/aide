"""Stdio server: reads NDJSON requests, dispatches to handlers, writes responses.

Handlers run on a small thread pool so a slow method never blocks the read loop
(cancel and ping must stay responsive). Writes are serialized by one lock. The
process exits when stdin closes, so a crashed shell never leaves an orphan core.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import IO, Any

from ai_desktop.core import protocol

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class Server:
    def __init__(self, reader: IO[str], writer: IO[str], handlers: dict[str, Handler], workers: int = 8):
        self._reader = reader
        self._writer = writer
        self._handlers = handlers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aide-core")
        self._write_lock = threading.Lock()
        self._stop = threading.Event()

    def send(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            self._writer.write(protocol.encode(message))
            self._writer.flush()

    def emit(self, name: str, data: dict[str, Any] | None = None) -> None:
        self.send(protocol.event(name, data))

    def stop(self) -> None:
        self._stop.set()

    def serve(self) -> None:
        for line in self._reader:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = protocol.decode_request(line)
            except protocol.ProtocolError as exc:
                self.send(protocol.error(None, exc.code, exc.message))
                continue
            self._pool.submit(self._dispatch, request)
        self._pool.shutdown(wait=True, cancel_futures=True)

    def _dispatch(self, request: protocol.Request) -> None:
        handler = self._handlers.get(request.method)
        if handler is None:
            self.send(protocol.error(request.id, protocol.ERR_UNKNOWN_METHOD, request.method))
            return
        try:
            self.send(protocol.result(request.id, handler(request.params)))
        except protocol.ProtocolError as exc:
            self.send(protocol.error(request.id, exc.code, exc.message))
        except Exception as exc:  # a handler bug must not kill the core
            logger.exception("handler %s failed", request.method)
            self.send(protocol.error(request.id, protocol.ERR_INTERNAL, str(exc)))


def make_stdio_server(handlers: dict[str, Handler]) -> Server:
    """Bind the protocol to the real stdout, then point ``sys.stdout`` at stderr.

    Stray ``print`` calls from any imported module then can never corrupt the stream.
    """
    writer = sys.stdout
    sys.stdout = sys.stderr
    return Server(sys.stdin, writer, handlers)
