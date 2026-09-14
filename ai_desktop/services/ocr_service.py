"""Apple Vision OCR results, runtime checks, and cancellable Qt tasks."""

from __future__ import annotations

import importlib
import re
import statistics
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from ai_desktop.utils.images import inspect_image


class OCRUnavailableError(RuntimeError):
    """Raised when the native OCR backend cannot be used."""


class OCRRecognitionError(RuntimeError):
    """Raised when Vision cannot process a valid image."""


class OCRStatus(str, Enum):
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class OCRRuntimeInfo:
    engine: str
    revision: int
    languages: tuple[str, ...]


@dataclass(frozen=True)
class OCRBounds:
    """Normalized Vision coordinates with the origin at the bottom left."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class OCRTextBlock:
    text: str
    confidence: float
    bounds: OCRBounds


@dataclass(frozen=True)
class OCRRequest:
    task_id: str
    image_path: str
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class OCRResult:
    task_id: str
    image_path: str
    status: OCRStatus
    blocks: tuple[OCRTextBlock, ...] = ()
    languages: tuple[str, ...] = ()
    elapsed_ms: float = 0.0
    error: str = ""

    @property
    def raw_text(self) -> str:
        return "\n".join(block.text for block in self.blocks)

    @property
    def text(self) -> str:
        return assemble_ocr_text(self.blocks)

    @property
    def ok(self) -> bool:
        return self.status in (OCRStatus.SUCCEEDED, OCRStatus.EMPTY)


_CODE_PREFIX = re.compile(
    r"^(?:async\s+def|def|class|if|elif|else|for|while|try|except|finally|with|"
    r"return|raise|yield|import|from|print|function|const|let|var|public|private)\b"
)


def assemble_ocr_text(blocks: Sequence[OCRTextBlock]) -> str:
    """Join raw blocks and infer leading spaces only for code-like content."""
    if not blocks:
        return ""
    rows = _group_blocks_into_rows(blocks)
    lines = [_join_ocr_row(row) for row in rows]
    code_score = sum(
        bool(_CODE_PREFIX.search(line.strip()))
        or any(token in line for token in ("()", "->", "{", "}", ";", "=="))
        for line in lines
    )
    if len(lines) < 2 or code_score < max(2, (len(lines) + 1) // 2):
        return "\n".join(lines)

    character_widths = [
        block.bounds.width / max(1, len(block.text))
        for block in blocks
        if block.text and block.bounds.width > 0
    ]
    if not character_widths:
        return "\n".join(lines)
    visible_width = statistics.median(character_widths)
    if visible_width <= 0:
        return "\n".join(lines)
    # Vision trims whitespace from each observation, but the resulting left
    # edges still form stable nesting columns. Merge small OCR jitter, then
    # use the smallest observed column step as one indentation level.
    columns: list[float] = []
    tolerance = visible_width * 0.75
    line_edges = [min(block.bounds.x for block in row) for row in rows]
    for x in sorted(line_edges):
        if columns and x - columns[-1] <= tolerance:
            columns[-1] = (columns[-1] + x) / 2
        else:
            columns.append(x)
    if len(columns) < 2:
        return "\n".join(lines)
    column_step = min(
        right - left for left, right in zip(columns, columns[1:])
    )
    if column_step <= tolerance:
        return "\n".join(lines)
    left_edge = columns[0]
    indent_width = 4 if any(line.rstrip().endswith(":") for line in lines) else 2
    formatted = []
    for text, x in zip(lines, line_edges):
        if text[:1].isspace():
            formatted.append(text)
            continue
        level = round((x - left_edge) / column_step)
        indentation = min(16, max(0, level * indent_width))
        formatted.append(" " * indentation + text)
    return "\n".join(formatted)


def _group_blocks_into_rows(
    blocks: Sequence[OCRTextBlock],
) -> list[list[OCRTextBlock]]:
    ordered = sorted(
        blocks,
        key=lambda block: (
            -(block.bounds.y + block.bounds.height / 2),
            block.bounds.x,
        ),
    )
    rows: list[list[OCRTextBlock]] = []
    row_centers: list[float] = []
    row_heights: list[float] = []
    for block in ordered:
        center = block.bounds.y + block.bounds.height / 2
        height = max(block.bounds.height, 0.001)
        if rows and abs(center - row_centers[-1]) <= max(
            height,
            row_heights[-1],
        ) * 0.45:
            rows[-1].append(block)
            count = len(rows[-1])
            row_centers[-1] = (row_centers[-1] * (count - 1) + center) / count
            row_heights[-1] = max(row_heights[-1], height)
        else:
            rows.append([block])
            row_centers.append(center)
            row_heights.append(height)
    for row in rows:
        row.sort(key=lambda block: block.bounds.x)
    return rows


def _join_ocr_row(row: Sequence[OCRTextBlock]) -> str:
    if not row:
        return ""
    text = row[0].text
    for previous, current in zip(row, row[1:]):
        widths = [
            block.bounds.width / max(1, len(block.text))
            for block in (previous, current)
            if block.text and block.bounds.width > 0
        ]
        character_width = statistics.median(widths) if widths else 0.01
        gap = current.bounds.x - (
            previous.bounds.x + previous.bounds.width
        )
        spaces = min(8, max(1, round(gap / max(character_width, 0.001))))
        text += " " * spaces + current.text
    return text


def _load_vision():
    if sys.platform != "darwin":
        raise OCRUnavailableError("Apple Vision OCR 仅支持 macOS。")
    try:
        return importlib.import_module("Vision")
    except (ImportError, OSError) as exc:
        raise OCRUnavailableError("Apple Vision OCR 组件未安装或无法加载。") from exc


def _autorelease_pool():
    """Release Objective-C objects created by a Python-owned worker thread."""
    try:
        return importlib.import_module("objc").autorelease_pool()
    except (ImportError, AttributeError):
        return nullcontext()


def probe_ocr_runtime() -> OCRRuntimeInfo:
    """Load Vision and query languages from the active recognition revision."""
    vision = _load_vision()
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


class OCRService:
    """Run one local Vision text-recognition request synchronously."""

    DEFAULT_LANGUAGES = ("en-US", "zh-Hans")

    def __init__(self, runtime: OCRRuntimeInfo | None = None) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> OCRRuntimeInfo:
        if self._runtime is None:
            self._runtime = probe_ocr_runtime()
        return self._runtime

    def recognize(
        self,
        request: OCRRequest,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> OCRResult:
        """Recognize a validated image, preserving Vision's raw block text."""
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            return self._cancelled_result(request)

        image_path = inspect_image(request.image_path).path
        if cancelled():
            return self._cancelled_result(request)
        runtime = self.runtime
        languages = self._resolve_languages(request.languages, runtime.languages)
        if cancelled():
            return self._cancelled_result(request)
        vision = _load_vision()
        try:
            foundation = importlib.import_module("Foundation")
            native_request = vision.VNRecognizeTextRequest.alloc().init()
            native_request.setRecognitionLevel_(
                vision.VNRequestTextRecognitionLevelAccurate
            )
            native_request.setRecognitionLanguages_(list(languages))
            native_request.setUsesLanguageCorrection_(False)
            if hasattr(native_request, "setAutomaticallyDetectsLanguage_"):
                native_request.setAutomaticallyDetectsLanguage_(True)
            handler = vision.VNImageRequestHandler.alloc().initWithURL_options_(
                foundation.NSURL.fileURLWithPath_(image_path), {}
            )
            started = time.perf_counter()
            succeeded, error = handler.performRequests_error_([native_request], None)
            elapsed_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:
            raise OCRRecognitionError("Apple Vision OCR 识别失败。") from exc
        if not succeeded:
            raise OCRRecognitionError(str(error or "Apple Vision OCR 未返回错误详情。"))
        if cancelled():
            return self._cancelled_result(request, elapsed_ms)

        blocks = self._extract_blocks(native_request.results() or ())
        status = OCRStatus.SUCCEEDED if blocks else OCRStatus.EMPTY
        return OCRResult(
            task_id=request.task_id,
            image_path=image_path,
            status=status,
            blocks=blocks,
            languages=languages,
            elapsed_ms=round(elapsed_ms, 1),
        )

    @classmethod
    def _resolve_languages(
        cls,
        requested: Sequence[str],
        supported: Sequence[str],
    ) -> tuple[str, ...]:
        supported_set = set(supported)
        desired = tuple(requested) or cls.DEFAULT_LANGUAGES
        languages = tuple(dict.fromkeys(
            language for language in desired if language in supported_set
        ))
        if not languages:
            raise OCRRecognitionError("当前系统不支持所选 OCR 语言。")
        return languages

    @staticmethod
    def _extract_blocks(observations) -> tuple[OCRTextBlock, ...]:
        blocks: list[OCRTextBlock] = []
        for observation in observations:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            box = observation.boundingBox()
            blocks.append(OCRTextBlock(
                text=str(candidate.string()),
                confidence=round(float(candidate.confidence()), 4),
                bounds=OCRBounds(
                    x=round(float(box.origin.x), 6),
                    y=round(float(box.origin.y), 6),
                    width=round(float(box.size.width), 6),
                    height=round(float(box.size.height), 6),
                ),
            ))
        blocks.sort(key=lambda block: (-block.bounds.y, block.bounds.x))
        return tuple(blocks)

    @staticmethod
    def _cancelled_result(
        request: OCRRequest,
        elapsed_ms: float = 0.0,
    ) -> OCRResult:
        return OCRResult(
            task_id=request.task_id,
            image_path=request.image_path,
            status=OCRStatus.CANCELLED,
            languages=request.languages,
            elapsed_ms=round(elapsed_ms, 1),
        )


class OCRWorker(QThread):
    """Run one OCR request away from the Qt GUI thread."""

    completed = pyqtSignal(object)

    def __init__(
        self,
        request: OCRRequest,
        service: OCRService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.request = request
        self._service = service or OCRService()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    def run(self) -> None:
        try:
            with _autorelease_pool():
                result = self._service.recognize(
                    self.request,
                    self._cancelled.is_set,
                )
        except Exception as exc:
            if self._cancelled.is_set():
                result = OCRService._cancelled_result(self.request)
            else:
                result = OCRResult(
                    task_id=self.request.task_id,
                    image_path=self.request.image_path,
                    status=OCRStatus.FAILED,
                    languages=self.request.languages,
                    error=str(exc) or "OCR 识别失败。",
                )
        if self._cancelled.is_set() and result.status != OCRStatus.CANCELLED:
            result = OCRService._cancelled_result(self.request, result.elapsed_ms)
        self.completed.emit(result)


class AsyncOCRService(QObject):
    """Own OCR workers and publish results only for the active task."""

    completed = pyqtSignal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        service: OCRService | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service or OCRService()
        self._active: OCRWorker | None = None
        self._retired: list[OCRWorker] = []

    @property
    def active_task_id(self) -> str | None:
        return self._active.request.task_id if self._active is not None else None

    def start(
        self,
        image_path: str | Path,
        languages: Sequence[str] = (),
    ) -> str:
        self.cancel()
        request = OCRRequest(
            task_id=f"ocr-{uuid.uuid4().hex}",
            image_path=str(image_path),
            languages=tuple(languages),
        )
        worker = OCRWorker(request, self._service, self)
        self._active = worker
        worker.completed.connect(self._on_completed)
        worker.finished.connect(self._on_finished)
        worker.start()
        return request.task_id

    def cancel(self) -> None:
        worker = self._active
        if worker is None:
            return
        self._active = None
        worker.cancel()
        if worker not in self._retired:
            self._retired.append(worker)

    def _on_completed(self, result: OCRResult) -> None:
        worker = self.sender()
        if worker is not self._active or result.task_id != self.active_task_id:
            return
        self.completed.emit(result)

    def _on_finished(self) -> None:
        worker = self.sender()
        if worker is self._active:
            self._active = None
        if worker in self._retired:
            self._retired.remove(worker)
        worker.deleteLater()

    def take_shutdown_workers(self) -> list[OCRWorker]:
        """Cancel work and transfer unfinished worker ownership to the controller."""
        self.cancel()
        workers = [worker for worker in self._retired if not worker.isFinished()]
        self._retired = []
        for worker in workers:
            worker.setParent(None)
        return workers
