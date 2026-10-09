"""Bound worker cleanup against real widget updates, including native deadlocks."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_worker_cleanup_does_not_block_widget_updates():
    # A native lock inversion cannot be interrupted by qtbot's Python timeout.
    # Keep this race regression in a child with a hard process-level bound.
    program = textwrap.dedent('''
        import faulthandler
        import os
        import tempfile
        import time
        from collections import deque
        faulthandler.dump_traceback_later(20)
        with tempfile.TemporaryDirectory(prefix='aide-worker-lifecycle-') as data:
            os.environ['AIDE_DATA_DIR'] = data
            from PyQt5.QtCore import QCoreApplication, QEvent, QTimer
            from PyQt5.QtTest import QTest
            from PyQt5.QtWidgets import QApplication, QLabel
            from ai_desktop import config
            from ai_desktop.llm.events import ResultStatus
            from ai_desktop.llm.run_worker import RunWorker
            from ai_desktop.services.search_credentials import SearchCredentials
            from ai_desktop.services.web_search import ENDPOINTS, SearchSettings
            from ai_desktop.utils import storage
            from tests.fake_ollama import FakeOllama
            app = QApplication([])
            storage.init_db()
            model, search = FakeOllama(), FakeOllama()
            config.OLLAMA_BASE_URL = model.url
            ENDPOINTS['exa'] = search.url + '/search'
            SearchCredentials.get = lambda *args, **kwargs: 'test-only-key'
            labels = deque(maxlen=128)
            pulses = [0]
            def update_widgets():
                pulses[0] += 1
                for _ in range(8):
                    label = QLabel()
                    label.setText(str(pulses[0]))
                    labels.append(label)
            timer = QTimer()
            timer.timeout.connect(update_widgets)
            timer.start(1)
            try:
                for index in range(24):
                    mode = index % 3
                    model.enqueue({'message': {'role': 'assistant', 'tool_calls': [
                        {'function': {'name': 'web_search', 'arguments': {'query': 'Python'}}}
                    ]}, 'done': True})
                    if mode == 2:
                        scenario = search.enqueue({'results': []}, before_headers=True)
                    else:
                        search.enqueue({'results': []}, status=401 if mode else 200)
                        model.enqueue({'message': {'content': 'finished'}, 'done': True})
                    worker = RunWorker([], '', agent_id='general_assistant',
                                       think=False, options={'num_ctx': 8192, 'num_predict': 1024},
                                       tools_admitted=True, search_settings=SearchSettings('exa', 1))
                    results = []
                    worker.done.connect(results.append)
                    worker.start()
                    deadline = time.monotonic() + 3
                    while not results and time.monotonic() < deadline:
                        QTest.qWait(5)
                        if mode == 2 and scenario.received.is_set():
                            worker.cancel()
                    assert len(results) == 1, (index, results)
                    assert worker.wait(2000), index
                    expected = ResultStatus.CANCELLED if mode == 2 else ResultStatus.SUCCEEDED
                    assert results[0].status == expected, (index, results[0])
                    worker.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                assert pulses[0] > 20, pulses[0]
                assert len(search.requests) == 24
                assert len(model.requests) == 40
            finally:
                timer.stop()
                timer.timeout.disconnect(update_widgets)
                model.close()
                search.close()
                storage.close_db()
            faulthandler.cancel_dump_traceback_later()
    ''')
    result = subprocess.run(
        [sys.executable, '-X', 'faulthandler', '-c', program],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
