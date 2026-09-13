"""Apple Vision OCR runtime, recognition, and stale-task checks."""

import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_desktop.services.ocr_service import (
    AsyncOCRService,
    OCRBounds,
    OCRRecognitionError,
    OCRRequest,
    OCRResult,
    OCRRuntimeInfo,
    OCRService,
    OCRStatus,
    OCRTextBlock,
    OCRUnavailableError,
    OCRWorker,
    probe_ocr_runtime,
)


class _Request:
    def revision(self):
        return 3

    def supportedRecognitionLanguagesAndReturnError_(self, _error):
        return (("en-US", "zh-Hans", "zh-Hant"), None)


class _RequestFactory:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return _Request()


def test_probe_reports_runtime_revision_and_languages(monkeypatch):
    fake_vision = SimpleNamespace(VNRecognizeTextRequest=_RequestFactory)
    monkeypatch.setattr("ai_desktop.services.ocr_service.sys.platform", "darwin")
    with patch(
        "ai_desktop.services.ocr_service.importlib.import_module",
        return_value=fake_vision,
    ):
        info = probe_ocr_runtime()

    assert info.engine == "apple-vision"
    assert info.revision == 3
    assert info.languages == ("en-US", "zh-Hans", "zh-Hant")


def test_probe_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr("ai_desktop.services.ocr_service.sys.platform", "linux")
    with pytest.raises(OCRUnavailableError, match="macOS"):
        probe_ocr_runtime()


def test_probe_reports_missing_framework(monkeypatch):
    monkeypatch.setattr("ai_desktop.services.ocr_service.sys.platform", "darwin")
    with patch(
        "ai_desktop.services.ocr_service.importlib.import_module",
        side_effect=ImportError("missing"),
    ), pytest.raises(OCRUnavailableError, match="未安装"):
        probe_ocr_runtime()


def test_probe_rejects_empty_language_list(monkeypatch):
    request = _Request()
    request.supportedRecognitionLanguagesAndReturnError_ = lambda _error: ((), None)
    factory = SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: request))
    fake_vision = SimpleNamespace(VNRecognizeTextRequest=factory)
    monkeypatch.setattr("ai_desktop.services.ocr_service.sys.platform", "darwin")
    with patch(
        "ai_desktop.services.ocr_service.importlib.import_module",
        return_value=fake_vision,
    ), pytest.raises(OCRUnavailableError, match="未返回"):
        probe_ocr_runtime()


class _Candidate:
    def __init__(self, text, confidence):
        self._text = text
        self._confidence = confidence

    def string(self):
        return self._text

    def confidence(self):
        return self._confidence


class _Observation:
    def __init__(self, text, x, y, confidence=0.95):
        self._candidate = _Candidate(text, confidence)
        self._box = SimpleNamespace(
            origin=SimpleNamespace(x=x, y=y),
            size=SimpleNamespace(width=0.3, height=0.1),
        )

    def topCandidates_(self, _count):
        return [self._candidate]

    def boundingBox(self):
        return self._box


class _NativeRequest:
    def __init__(self, observations):
        self._observations = observations
        self.languages = []
        self.automatic = False

    def setRecognitionLevel_(self, _level):
        pass

    def setRecognitionLanguages_(self, languages):
        self.languages = languages

    def setUsesLanguageCorrection_(self, _enabled):
        pass

    def setAutomaticallyDetectsLanguage_(self, enabled):
        self.automatic = enabled

    def results(self):
        return self._observations


class _NativeRequestFactory:
    def __init__(self, request):
        self._request = request

    def alloc(self):
        return SimpleNamespace(init=lambda: self._request)


class _Handler:
    def performRequests_error_(self, _requests, _error):
        return True, None


class _HandlerFactory:
    @classmethod
    def alloc(cls):
        return cls()

    def initWithURL_options_(self, _url, _options):
        return _Handler()


def test_recognize_returns_sorted_raw_blocks_and_metadata(monkeypatch):
    native_request = _NativeRequest([
        _Observation("second", 0.05, 0.2, 0.81),
        _Observation("first", 0.1, 0.8),
    ])
    vision = SimpleNamespace(
        VNRecognizeTextRequest=_NativeRequestFactory(native_request),
        VNRequestTextRecognitionLevelAccurate=1,
        VNImageRequestHandler=_HandlerFactory,
    )
    foundation = SimpleNamespace(
        NSURL=SimpleNamespace(fileURLWithPath_=lambda value: value),
    )
    runtime = OCRRuntimeInfo("apple-vision", 3, ("en-US", "zh-Hans"))
    service = OCRService(runtime)
    monkeypatch.setattr(
        "ai_desktop.services.ocr_service.inspect_image",
        lambda path: SimpleNamespace(path=str(path)),
    )
    monkeypatch.setattr("ai_desktop.services.ocr_service._load_vision", lambda: vision)
    monkeypatch.setattr(
        "ai_desktop.services.ocr_service.importlib.import_module",
        lambda name: foundation if name == "Foundation" else None,
    )

    result = service.recognize(OCRRequest("task-1", "/managed/image.png"))

    assert result.status == OCRStatus.SUCCEEDED
    assert result.ok
    assert result.text == "first\nsecond"
    assert result.languages == ("en-US", "zh-Hans")
    assert result.blocks[0].confidence == 0.95
    assert result.blocks[0].bounds == OCRBounds(0.1, 0.8, 0.3, 0.1)
    assert native_request.languages == ["en-US", "zh-Hans"]
    assert native_request.automatic is True
    assert result.elapsed_ms >= 0


def test_recognize_rejects_unsupported_languages(monkeypatch):
    runtime = OCRRuntimeInfo("apple-vision", 3, ("en-US",))
    service = OCRService(runtime)
    monkeypatch.setattr(
        "ai_desktop.services.ocr_service.inspect_image",
        lambda path: SimpleNamespace(path=str(path)),
    )
    with pytest.raises(OCRRecognitionError, match="不支持"):
        service.recognize(OCRRequest("task", "image.png", ("zh-Hans",)))


def _success(request, text="recognized"):
    return OCRResult(
        request.task_id,
        request.image_path,
        OCRStatus.SUCCEEDED,
        (OCRTextBlock(text, 1.0, OCRBounds(0.0, 0.0, 1.0, 1.0)),),
        request.languages,
        12.5,
    )


class _BlockingOCRService:
    def __init__(self):
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def recognize(self, request, _is_cancelled):
        if request.image_path == "first.png":
            self.first_started.set()
            self.release_first.wait(2)
        return _success(request)


def test_worker_converts_service_error_to_failed_result(qtbot):
    service = SimpleNamespace(
        recognize=lambda request, cancelled: (_ for _ in ()).throw(
            OCRRecognitionError("bad image")
        ),
    )
    worker = OCRWorker(OCRRequest("task", "image.png"), service)
    with qtbot.waitSignal(worker.completed, timeout=1000) as signal:
        worker.start()
    worker.wait(1000)
    assert signal.args[0].status == OCRStatus.FAILED
    assert signal.args[0].error == "bad image"
    worker.deleteLater()


def test_async_service_replaces_task_and_drops_stale_result(qtbot):
    backend = _BlockingOCRService()
    service = AsyncOCRService(service=backend)
    delivered = []
    service.completed.connect(delivered.append)

    old_task = service.start("first.png")
    qtbot.waitUntil(backend.first_started.is_set, timeout=1000)
    new_task = service.start("second.png")
    qtbot.waitUntil(lambda: len(delivered) == 1, timeout=1000)
    backend.release_first.set()
    qtbot.waitUntil(lambda: not service._retired, timeout=1000)

    assert old_task != new_task
    assert [result.task_id for result in delivered] == [new_task]
    assert delivered[0].text == "recognized"
    service.deleteLater()


def test_async_service_cancel_suppresses_late_result(qtbot):
    backend = _BlockingOCRService()
    service = AsyncOCRService(service=backend)
    delivered = []
    service.completed.connect(delivered.append)

    service.start("first.png")
    qtbot.waitUntil(backend.first_started.is_set, timeout=1000)
    service.cancel()
    assert service.active_task_id is None
    backend.release_first.set()
    qtbot.waitUntil(lambda: not service._retired, timeout=1000)

    assert delivered == []
    service.deleteLater()
