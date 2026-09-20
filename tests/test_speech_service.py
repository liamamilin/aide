"""Tests for the optional, asynchronous selected-text speech service."""

from ai_desktop.services.speech_service import SpeechService


def test_empty_selection_is_rejected(qtbot):
    service = SpeechService()
    with qtbot.waitSignal(service.completed, timeout=1000) as signal:
        assert service.speak("  \n\t ") is False
    assert signal.args == [False, "没有读取到选中文字，请重新选择后再试。"]


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
