"""Runtime capability checks for the macOS Vision OCR backend."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass


class OCRUnavailableError(RuntimeError):
    """Raised when the native OCR backend cannot be used."""


@dataclass(frozen=True)
class OCRRuntimeInfo:
    engine: str
    revision: int
    languages: tuple[str, ...]


def probe_ocr_runtime() -> OCRRuntimeInfo:
    """Load Vision and query languages from the active recognition revision."""
    if sys.platform != "darwin":
        raise OCRUnavailableError("Apple Vision OCR 仅支持 macOS。")
    try:
        vision = importlib.import_module("Vision")
    except (ImportError, OSError) as exc:
        raise OCRUnavailableError("Apple Vision OCR 组件未安装或无法加载。") from exc

    try:
        request = vision.VNRecognizeTextRequest.alloc().init()
        languages, error = request.supportedRecognitionLanguagesAndReturnError_(None)
    except Exception as exc:
        raise OCRUnavailableError("Apple Vision OCR 运行时检查失败。") from exc
    if error is not None:
        raise OCRUnavailableError(f"Apple Vision OCR 无法读取支持语言：{error}")
    normalized = tuple(str(language) for language in (languages or ()))
    if not normalized:
        raise OCRUnavailableError("Apple Vision OCR 未返回任何可用语言。")
    return OCRRuntimeInfo(
        engine="apple-vision",
        revision=int(request.revision()),
        languages=normalized,
    )
