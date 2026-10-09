"""Repeat Qt worker/reply lifecycle stress against loopback services only.

A nonzero exit reports a regression. Success covers the selected rounds;
deterministic regressions and native address-reuse checks are also required.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

PROGRAM = r'''
import faulthandler
import gc
import sys
import os
import tempfile
import time
from collections import deque
task_count = int(sys.argv[1])
gc_interval = int(sys.argv[2])
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
        if pulses[0] % gc_interval == 0:
            gc.collect()
        for _ in range(8):
            label = QLabel()
            label.setText(str(pulses[0]))
            labels.append(label)
    timer = QTimer()
    timer.timeout.connect(update_widgets)
    timer.start(1)
    try:
        for index in range(task_count):
            mode = index % 3
            model.enqueue({'message': {'content': ''}}, {'message': {'role': 'assistant', 'tool_calls': [
                {'function': {'name': 'web_search', 'arguments': {'query': 'Python'}}}
            ]}, 'done': True}, delay=.02)
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
        assert len(search.requests) == task_count
        assert len(model.requests) == task_count // 3 * 5
        print(f'Tasks={task_count} model_http={len(model.requests)} search_http={len(search.requests)} '
              f'widget_pulses={pulses[0]}', flush=True)
    finally:
        timer.stop()
        timer.timeout.disconnect(update_widgets)
        model.close()
        search.close()
        storage.close_db()
    faulthandler.cancel_dump_traceback_later()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=10)
    parser.add_argument('--tasks', type=int, default=96)
    parser.add_argument('--gc-interval', type=int, default=2)
    args = parser.parse_args()
    if args.rounds < 1 or args.tasks < 3 or args.tasks % 3 or args.gc_interval < 1:
        parser.error('Use positive rounds/gc interval and a positive task count divisible by 3')
    root = Path(__file__).resolve().parents[1]
    for index in range(args.rounds):
        try:
            result = subprocess.run(
                [sys.executable, '-X', 'faulthandler', '-c', PROGRAM,
                 str(args.tasks), str(args.gc_interval)],
                cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=25,
            )
        except subprocess.TimeoutExpired as exc:
            print(f'Round {index + 1}: process timeout', file=sys.stderr)
            for output in (exc.stdout, exc.stderr):
                if output:
                    print(output.decode(errors='replace') if isinstance(output, bytes) else output,
                          file=sys.stderr)
            return 124
        print(f'Round {index + 1}: exit {result.returncode}', flush=True)
        if result.stdout:
            print(result.stdout, end='', flush=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return 1
    print(f'Passed {args.rounds} rounds; no duplicate HTTP requests or unexpected terminal results.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
