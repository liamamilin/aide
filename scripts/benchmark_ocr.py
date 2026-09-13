#!/usr/bin/env python3
"""Run a small, synthetic Apple Vision OCR benchmark without private images."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import Vision
from Foundation import NSURL
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

from ai_desktop.services.ocr_service import probe_ocr_runtime

SAMPLES = {
    "english": {
        "font": "Menlo",
        "lines": [
            "Local OCR keeps screenshots private.",
            "Copy text, then ask the assistant.",
        ],
    },
    "chinese": {
        "font": "Songti SC",
        "lines": [
            "本地识字保护截图隐私。",
            "提取文字后可以复制或提问。",
        ],
    },
    "mixed_code": {
        "font": "STHeiti",
        "lines": [
            "def greet(name: str) -> str:",
            "    return f\"你好, {name}!\"",
            "print(greet(\"Codex\"))",
        ],
    },
    "blank": {"font": "Menlo", "lines": []},
}


def _distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        for right_index, right_char in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        previous = current
    return previous[-1]


def _make_sample(path: Path, font_name: str, lines: list[str]) -> str:
    image = QImage(1400, 520, QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setPen(QColor("#111827"))
    painter.setFont(QFont(font_name, 34))
    for index, line in enumerate(lines):
        painter.drawText(70, 95 + index * 82, line)
    painter.end()
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not create OCR sample: {path}")
    return "\n".join(lines)


def _recognize(path: Path, languages: tuple[str, ...]) -> dict:
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    preferred = [code for code in ("en-US", "zh-Hans") if code in languages]
    request.setRecognitionLanguages_(preferred)
    request.setUsesLanguageCorrection_(False)
    if hasattr(request, "setAutomaticallyDetectsLanguage_"):
        request.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(str(path)), {}
    )
    started = time.perf_counter()
    succeeded, error = handler.performRequests_error_([request], None)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not succeeded:
        raise RuntimeError(str(error or "Vision request failed without an error"))

    rows = []
    for observation in request.results() or ():
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        box = observation.boundingBox()
        rows.append({
            "text": str(candidate.string()),
            "confidence": round(float(candidate.confidence()), 4),
            "x": round(float(box.origin.x), 6),
            "y": round(float(box.origin.y), 6),
            "width": round(float(box.size.width), 6),
            "height": round(float(box.size.height), 6),
        })
    rows.sort(key=lambda row: (-row["y"], row["x"]))
    return {
        "elapsed_ms": round(elapsed_ms, 1),
        "text": "\n".join(row["text"] for row in rows),
        "blocks": rows,
    }


def run_benchmark(output_dir: Path) -> dict:
    app = QGuiApplication.instance() or QGuiApplication([])
    runtime = probe_ocr_runtime()
    samples = {}
    for name, spec in SAMPLES.items():
        path = output_dir / f"{name}.png"
        truth = _make_sample(path, spec["font"], spec["lines"])
        result = _recognize(path, runtime.languages)
        denominator = max(1, len(truth))
        result["truth"] = truth
        result["cer"] = round(_distance(truth, result["text"]) / denominator, 4)
        samples[name] = result
    app.processEvents()
    return {
        "runtime": asdict(runtime),
        "platform": os.uname().release,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON results to this file")
    parser.add_argument("--samples", type=Path, help="Keep generated samples in this directory")
    args = parser.parse_args()
    if args.samples:
        args.samples.mkdir(parents=True, exist_ok=True)
        result = run_benchmark(args.samples)
    else:
        with tempfile.TemporaryDirectory(prefix="aide-ocr-") as directory:
            result = run_benchmark(Path(directory))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
