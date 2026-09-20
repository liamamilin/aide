"""Tests for the optional, asynchronous selected-text speech service."""

from ai_desktop.services.speech_service import SpeechService, SpeechWorker


def test_empty_selection_is_rejected(qtbot):
    service = SpeechService()
    with qtbot.waitSignal(service.completed, timeout=1000) as signal:
        assert service.speak("  \n\t ") is False
    assert signal.args == [False, "没有读取到选中文字，请重新选择后再试。"]


def test_chinese_selection_is_rejected_with_clear_message(qtbot):
    service = SpeechService()
    with qtbot.waitSignal(service.completed, timeout=1000) as signal:
        assert service.speak("这是一个中文句子。") is False
    assert signal.args == [
        False,
        "当前朗读只支持英文，请只选择英文单词或句子。",
    ]


def test_mixed_chinese_and_english_selection_is_rejected(qtbot):
    service = SpeechService()
    with qtbot.waitSignal(service.completed, timeout=1000) as signal:
        assert service.speak("Hello，这是一句英文。") is False
    assert signal.args[0] is False


def test_text_is_trimmed_and_length_limited(monkeypatch, qtbot):
    service = SpeechService()
    captured = {}

    class FakeWorker:
        def __init__(self, _service, text, _parent):
            captured["text"] = text
            self.completed = _Signal()
            self.progress = _Signal()
            self.finished = _Signal()

        def start(self):
            pass

    class _Signal:
        def connect(self, _slot):
            pass

    monkeypatch.setattr("ai_desktop.services.speech_service.SpeechWorker", FakeWorker)
    assert service.speak("  hello   world  ") is True
    assert captured["text"] == "hello world"


def test_worker_uses_large_native_stack_for_torch():
    worker = SpeechWorker(SpeechService(), "hello")
    assert worker.stackSize() >= 16 * 1024 * 1024
    worker.deleteLater()


def test_second_request_cancels_without_overlapping_workers(monkeypatch):
    service = SpeechService()
    workers = []

    class FakeWorker:
        def __init__(self, _service, text, _parent):
            self.text = text
            self.cancelled = False
            self.completed = _Signal()
            self.progress = _Signal()
            self.finished = _Signal()
            workers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def isRunning(self):
            return not self.cancelled

    class _Signal:
        def connect(self, _slot):
            pass

    monkeypatch.setattr("ai_desktop.services.speech_service.SpeechWorker", FakeWorker)
    assert service.speak("first") is True
    assert service.speak("second") is True
    assert len(workers) == 1
    assert workers[0].cancelled is True
    assert service._pending_text == "second"


def test_worker_contains_base_exception_without_leaking_from_qthread():
    service = SpeechService()
    worker = SpeechWorker(service, "hello")
    messages = []
    worker.completed.connect(lambda success, message: messages.append((success, message)))

    def fail(_text, _worker):
        raise KeyboardInterrupt("synthetic worker failure")

    service._synthesize_and_play = fail
    worker.run()
    assert messages == [(False, "synthetic worker failure")]
    worker.deleteLater()
