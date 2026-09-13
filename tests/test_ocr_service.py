"""Apple Vision OCR runtime capability checks."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_desktop.services.ocr_service import OCRUnavailableError, probe_ocr_runtime


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
