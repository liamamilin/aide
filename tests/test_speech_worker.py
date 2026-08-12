"""Tests for lazy MLX speech generation and audio playback."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from ai_desktop.tts.speech_worker import SpeechRequest, SpeechWorker


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.started = False
        self.closed = False
        self.aborted = False

    def start(self):
        self.started = True

    def write(self, audio):
        self.writes.append(audio.copy())

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


class FakeArray(list):
    @property
    def size(self):
        return len(self)

    @property
    def shape(self):
        return (len(self),)

    def reshape(self, *_shape):
        return self

    def copy(self):
        return FakeArray(self)


def _install_fake_dependencies(monkeypatch):
    model = MagicMock()
    model.sample_rate = 24000
    model.generate.side_effect = lambda **kwargs: iter([
        SimpleNamespace(audio=[0.1, 0.2]),
        SimpleNamespace(audio=[0.3]),
    ])
    load_model = MagicMock(return_value=model)

    mlx_audio = ModuleType("mlx_audio")
    tts = ModuleType("mlx_audio.tts")
    utils = ModuleType("mlx_audio.tts.utils")
    utils.load_model = load_model
    mlx_audio.tts = tts
    tts.utils = utils
    monkeypatch.setitem(sys.modules, "mlx_audio", mlx_audio)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts", tts)
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", utils)

    mlx = ModuleType("mlx")
    mlx_core = ModuleType("mlx.core")
    mlx_core.clear_cache = MagicMock()
    mlx.core = mlx_core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mlx_core)

    numpy = ModuleType("numpy")
    numpy.float32 = "float32"
    numpy.asarray = lambda value, dtype=None: FakeArray(value)
    numpy.concatenate = lambda values: FakeArray(
        item for value in values for item in value
    )
    monkeypatch.setitem(sys.modules, "numpy", numpy)

    sounddevice = ModuleType("sounddevice")
    streams = []

    def output_stream(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    sounddevice.OutputStream = output_stream
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    return model, load_model, streams, mlx_core


def test_process_loads_once_and_streams_audio(monkeypatch, qtbot):
    model, load_model, streams, _mlx_core = _install_fake_dependencies(monkeypatch)
    worker = SpeechWorker("test/model")
    request = SpeechRequest("hello", "Aiden", "English")

    assert worker._process(request)
    assert worker._process(request)

    load_model.assert_called_once_with("test/model")
    assert len(streams) == 2
    assert all(stream.started and stream.closed for stream in streams)
    assert sum(len(stream.writes) for stream in streams) == 2
    assert all(len(stream.writes[0]) == 3 for stream in streams)
    model.generate.assert_called_with(
        text="hello",
        voice="Aiden",
        lang_code="English",
        stream=False,
    )


def test_process_reports_missing_dependency(monkeypatch, qtbot):
    monkeypatch.setitem(sys.modules, "mlx_audio", None)
    worker = SpeechWorker("test/model")
    with qtbot.waitSignal(worker.error, timeout=1000) as signal:
        assert not worker._process(SpeechRequest("hello", "Aiden", "English"))
    assert "未安装" in signal.args[0]


def test_idle_timeout_unloads_and_next_request_reloads(monkeypatch, qtbot):
    _model, load_model, _streams, mlx_core = _install_fake_dependencies(monkeypatch)
    worker = SpeechWorker("test/model", idle_timeout_seconds=0.02)

    with qtbot.waitSignal(worker.speech_finished, timeout=1000):
        worker.speak("first", "Aiden", "English")
    qtbot.waitUntil(lambda: worker._model is None, timeout=1000)
    mlx_core.clear_cache.assert_called_once_with()

    with qtbot.waitSignal(worker.speech_finished, timeout=1000):
        worker.speak("second", "Aiden", "English")
    assert load_model.call_count == 2
    assert worker.shutdown()


def test_stop_aborts_active_audio_stream():
    worker = SpeechWorker("test/model")
    stream = FakeStream()
    worker._stream = stream
    worker.stop_speaking()
    assert worker._cancelled.is_set()
    assert stream.aborted


def test_friendly_disk_error():
    message = SpeechWorker._friendly_error(OSError("No space left on device"))
    assert "磁盘空间不足" in message
