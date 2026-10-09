"""Delay the PyQt monitor, reuse a native reply address, then deliver its old notification.

Manual mechanism check, separate from deterministic pytest regressions. Only
loopback services and temporary data are used. No reused address within the
allocation bound is an inconclusive failure, never a passing check.
"""
import argparse
import faulthandler
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def check_reuse(kind):
    with tempfile.TemporaryDirectory(prefix='aide-native-reuse-') as data:
        os.environ['AIDE_DATA_DIR'] = data
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from PyQt5 import sip
        from PyQt5.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QUrl
        from PyQt5.QtNetwork import QNetworkRequest
        from PyQt5.QtWidgets import QApplication

        from ai_desktop.llm.chat_client import ChatClient
        from ai_desktop.llm.qt_stream import QtChatTransport
        from ai_desktop.services.qt_search import SearchJob
        from ai_desktop.services.web_search import ENDPOINTS, SearchSettings, build_request
        from ai_desktop.utils import storage
        from tests.fake_ollama import FakeOllama

        app = QApplication([])
        storage.init_db()
        server = FakeOllama()
        if kind == 'model':
            server.enqueue({'message': {'content': 'native answer'}, 'done': True})
            request = ChatClient(base_url=server.url).create_request([])
        else:
            ENDPOINTS['exa'] = server.url + '/search'
            server.enqueue({'results': [{'title': 'native answer', 'url': 'https://docs.python.org/'}]})
            request = build_request(SearchSettings('exa', 1), 'test-only-key', 'Python')
        ready, resume = threading.Event(), threading.Event()

        class Worker(QThread):
            def run(self):
                self.failure, self.result = None, None
                retired, addresses = [], set()
                owner = None
                try:
                    for attempt in range(512):
                        owner = QtChatTransport(request) if kind == 'model' else SearchJob(request)
                        old = owner._manager.post(QNetworkRequest(QUrl(server.url + '/retired')), b'{}')
                        addresses.add(sip.unwrapinstance(old))
                        old.abort()
                        sip.delete(old)
                        retired.append(old)
                        owner.start(b'{}') if kind == 'model' else owner.start()
                        address = sip.unwrapinstance(owner._reply)
                        if address in addresses:
                            print(f'{kind}: native address reused at attempt {attempt + 1}', flush=True)
                            break
                        addresses.add(address)
                        owner.cancel()
                        owner.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                        owner = None
                    else:
                        raise AssertionError('No address reused within allocation bound; inconclusive')
                    ready.set()
                    assert resume.wait(5), 'Main-thread gate timeout'
                    # No sip.setdeleted injection: the actual native PyQt
                    # monitor has processed an old destroyed(QObject*) event.
                    assert sip.isdeleted(owner._reply), 'Queued monitor did not invalidate the live wrapper'
                    print(f'{kind}: queued monitor invalidated the live wrapper', flush=True)
                    loop = QEventLoop()
                    terminal = owner.done if kind == 'model' else owner.finished
                    terminal.connect(loop.quit)
                    owner._headers_received() if kind == 'model' else owner._read()
                    if owner.result is None:
                        loop.exec_()
                    self.result = owner.result
                    terminal.disconnect(loop.quit)
                except Exception as exc:
                    self.failure = repr(exc)
                finally:
                    if owner is not None:
                        owner.cancel()
                        owner.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    ready.set()

        worker = Worker()
        faulthandler.dump_traceback_later(20)
        try:
            worker.start()
            # Keep UI events pending until a new reply occupies an old address.
            assert ready.wait(8), 'Worker preparation timeout'
            assert worker.failure is None, worker.failure
            QCoreApplication.sendPostedEvents(None, 0)
            app.processEvents()
            resume.set()
            assert worker.wait(5000), 'Worker did not finish'
            assert worker.failure is None, worker.failure
            assert worker.result.ok, worker.result
            answer = worker.result.text if kind == 'model' else worker.result.sources[0].title
            assert answer == 'native answer', worker.result
            assert len(server.requests) == 1, server.requests
            print(f'{kind}: succeeded, HTTP requests=1', flush=True)
        finally:
            resume.set()
            worker.wait(5000)
            server.close()
            storage.close_db()
            faulthandler.cancel_dump_traceback_later()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--kind', choices=('model', 'search'), default='model')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('Use positive rounds')
    if args.child:
        check_reuse(args.kind)
        return 0
    for index in range(args.rounds):
        try:
            result = subprocess.run(
                [sys.executable, '-X', 'faulthandler', str(Path(__file__).resolve()),
                 '--child', '--kind', args.kind],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=25,
            )
        except subprocess.TimeoutExpired:
            print(f'Round {index + 1}: process timeout', file=sys.stderr)
            return 124
        print(f'Round {index + 1}: exit {result.returncode}', flush=True)
        print(result.stdout, end='', flush=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
