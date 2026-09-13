"""Loopback HTTP service for real QtNetwork tests; never contacts a model."""
import json
import select
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue


@dataclass
class Scenario:
    chunks: list[bytes]
    status: int = 200
    before_headers: bool = False
    hold_open: bool = False
    delay: float = 0
    content_length: int | None = None
    received: threading.Event = field(default_factory=threading.Event)
    sent: threading.Event = field(default_factory=threading.Event)
    disconnected: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)


class FakeOllama:
    def __init__(self):
        self.scenarios = Queue()
        self.requests = []
        self.stopping = threading.Event()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                scenario = service.scenarios.get(timeout=2)
                try:
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    service.requests.append({"path": self.path, "payload": json.loads(body)})
                    scenario.received.set()
                    self.send_scenario(scenario, "application/x-ndjson")
                except (BrokenPipeError, ConnectionResetError):
                    scenario.disconnected.set()
                finally:
                    scenario.closed.set()

            def do_GET(self):
                scenario = service.scenarios.get(timeout=2)
                try:
                    service.requests.append({"method": "GET", "path": self.path})
                    scenario.received.set()
                    self.send_scenario(scenario, "application/json")
                except (BrokenPipeError, ConnectionResetError):
                    scenario.disconnected.set()
                finally:
                    scenario.closed.set()

            def send_scenario(self, scenario, content_type):
                if scenario.before_headers:
                    self.wait_for_disconnect(scenario)
                    return
                self.send_response(scenario.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                if scenario.content_length is not None:
                    self.send_header("Content-Length", str(scenario.content_length))
                self.end_headers()
                for chunk in scenario.chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if scenario.delay:
                        service.stopping.wait(scenario.delay)
                scenario.sent.set()
                if scenario.hold_open:
                    self.wait_for_disconnect(scenario)

            def wait_for_disconnect(self, scenario):
                while not service.stopping.is_set():
                    readable, _, _ = select.select([self.connection], [], [], 0.02)
                    if readable and not self.connection.recv(1):
                        scenario.disconnected.set()
                        return

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def enqueue(self, *events, chunks=None, **kwargs):
        if chunks is None:
            chunks = [(json.dumps(event, ensure_ascii=False) + "\n").encode() for event in events]
        scenario = Scenario(chunks, **kwargs)
        self.scenarios.put(scenario)
        return scenario

    def close(self):
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
