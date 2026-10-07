"""Selection copying waits for release without reading or changing user data."""
from PyQt5.QtCore import QTimer

from ai_desktop.capture.clipboard_monitor import SelectionCaptureTask
from tests.test_clipboard_monitor import FakePasteboard


def task_for(qtbot, probe, *, timeout=500):
    copies = []
    board = FakePasteboard()
    task = SelectionCaptureTask(copy_action=lambda: copies.append(True) or True, pasteboard=board,
                                modifier_state=probe, release_wait_ms=timeout)
    snapshots = []
    original = board.snapshot
    def snapshot():
        snapshots.append(True)
        return original()
    board.snapshot = snapshot
    task._start_command = lambda program, args, **kwargs: QTimer.singleShot(
        0, lambda: kwargs['callback'](True, '中文选区 English selection', ''))
    return task, copies, snapshots


def test_holding_modifiers_longer_than_100ms_does_not_copy_or_snapshot(qtbot):
    state = [True]
    task, copies, snapshots = task_for(qtbot, lambda: state[0])
    task.start()
    pulse = []
    QTimer.singleShot(20, lambda: pulse.append(True))
    qtbot.wait(150)
    assert pulse and not copies and not snapshots
    with qtbot.waitSignal(task.completed) as signal:
        state[0] = False
    assert copies == [True] and snapshots == [True]
    assert signal.args == ['中文选区 English selection']


def test_modifiers_timeout_does_not_touch_clipboard(qtbot):
    task, copies, snapshots = task_for(qtbot, lambda: True, timeout=50)
    with qtbot.waitSignal(task.completed) as signal:
        task.start()
    assert signal.args == [''] and task.failure_reason == 'modifiers_held'
    assert not copies and not snapshots and not task._pasteboard.restores


def test_cancel_during_release_wait_is_terminal_without_clipboard_side_effect(qtbot):
    state = [True]
    task, copies, snapshots = task_for(qtbot, lambda: state[0])
    emitted = []
    task.completed.connect(emitted.append)
    task.start()
    task.cancel()
    state[0] = False
    qtbot.wait(100)
    assert emitted == [''] and not copies and not snapshots


def test_unavailable_modifier_state_fails_without_injecting_copy(qtbot):
    def unavailable():
        raise RuntimeError('native modifier state unavailable')
    task, copies, snapshots = task_for(qtbot, unavailable)
    with qtbot.waitSignal(task.completed) as signal:
        task.start()
    assert signal.args == [''] and task.failure_reason == 'modifier_state_unavailable'
    assert not copies and not snapshots


def test_modifier_pressed_during_clipboard_snapshot_prevents_copy(qtbot):
    state = [False]
    task, copies, snapshots = task_for(qtbot, lambda: state[0])
    original = task._pasteboard.snapshot
    def slow_snapshot():
        result = original()
        state[0] = True
        return result
    task._pasteboard.snapshot = slow_snapshot
    with qtbot.waitSignal(task.completed) as signal:
        task.start()
    assert signal.args == [''] and task.failure_reason == 'modifiers_held'
    assert snapshots and not copies and not task._pasteboard.restores
