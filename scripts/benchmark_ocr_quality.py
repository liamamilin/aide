#!/usr/bin/env python3
"""Run the fixed 50-image F02 OCR quality suite on Apple Vision."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

from ai_desktop.services.ocr_service import OCRRequest, OCRService

ENGLISH_TEXTS = (
    ("Local OCR keeps screenshots private.", "Copy the result before asking a question."),
    ("The service is running on this Mac.", "No remote vision model is required."),
    ("Select an image and extract its text.", "Review the result before you send it."),
    ("Connection refused on port 11434.", "Check whether Ollama is running."),
    ("A clear error message saves debugging time.", "Try the request again after recovery."),
)

CHINESE_TEXTS = (
    ("本地识字不会上传截图。", "请在发送前检查识别结果。"),
    ("服务暂时无法连接。", "请确认本地模型已经启动。"),
    ("选择图片后点击识字。", "结果可以编辑、复制或提问。"),
    ("这是一段固定质量样本。", "它不包含任何私人屏幕内容。"),
    ("图片中的文字已经提取。", "低置信度内容需要人工核对。"),
)

CODE_TEXTS = (
    ("def greet(name: str) -> str:", '    return f"你好, {name}!"', 'print(greet("Codex"))'),
    ("if response.status_code == 200:", '    print("请求成功")', "else:", '    print("请重试")'),
    ("for item in results:", '    label = f"结果: {item}"', "    print(label)"),
    ("class LocalOCR:", "    def run(self, image):", "        return self.vision(image)"),
    ("try:", "    text = recognize(image)", "except RuntimeError:", '    text = "识别失败"'),
)

FONT_PROFILES = (
    ("Menlo", 28, "#FFFFFF", "#111827"),
    ("PingFang SC", 34, "#F8FAFC", "#172033"),
    ("Songti SC", 40, "#FFFFFF", "#262626"),
    ("STHeiti", 32, "#111827", "#F9FAFB"),
)


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


def _render(path: Path, lines: tuple[str, ...], profile_index: int) -> str:
    font_name, font_size, background, foreground = FONT_PROFILES[profile_index]
    image = QImage(1500, 560, QImage.Format_RGB32)
    image.fill(QColor(background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setPen(QColor(foreground))
    painter.setFont(QFont(font_name, font_size))
    line_height = max(70, font_size * 2)
    for index, line in enumerate(lines):
        painter.drawText(72, 96 + index * line_height, line)
    painter.end()
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Could not create OCR quality sample: {path}")
    return "\n".join(lines)


def _cases():
    for category, texts, repeat in (
        ("english", ENGLISH_TEXTS, 4),
        ("chinese", CHINESE_TEXTS, 4),
        ("mixed_code", CODE_TEXTS, 2),
    ):
        index = 0
        for repeat_index in range(repeat):
            for lines in texts:
                index += 1
                yield category, index, lines, repeat_index % len(FONT_PROFILES)


def run_quality_suite(output_dir: Path) -> dict:
    app = QGuiApplication.instance() or QGuiApplication([])
    service = OCRService()
    samples = []
    totals = {
        "english": {"edits": 0, "characters": 0, "elapsed_ms": 0.0, "count": 0},
        "chinese": {"edits": 0, "characters": 0, "elapsed_ms": 0.0, "count": 0},
        "mixed_code": {"edits": 0, "characters": 0, "elapsed_ms": 0.0, "count": 0},
    }
    for category, index, lines, profile_index in _cases():
        sample_id = f"{category}-{index:02d}"
        path = output_dir / f"{sample_id}.png"
        truth = _render(path, lines, profile_index)
        result = service.recognize(OCRRequest(sample_id, str(path)))
        edits = _distance(truth, result.text)
        cer = edits / max(1, len(truth))
        summary = totals[category]
        summary["edits"] += edits
        summary["characters"] += len(truth)
        summary["elapsed_ms"] += result.elapsed_ms
        summary["count"] += 1
        samples.append({
            "id": sample_id,
            "category": category,
            "font_profile": FONT_PROFILES[profile_index],
            "truth": truth,
            "text": result.text,
            "raw_text": result.raw_text,
            "cer": round(cer, 4),
            "elapsed_ms": result.elapsed_ms,
            "blocks": [asdict(block) for block in result.blocks],
        })

    thresholds = {"english": 0.05, "chinese": 0.05, "mixed_code": 0.10}
    categories = {}
    for category, summary in totals.items():
        cer = summary["edits"] / max(1, summary["characters"])
        categories[category] = {
            "count": summary["count"],
            "characters": summary["characters"],
            "cer": round(cer, 4),
            "threshold": thresholds[category],
            "passed": cer <= thresholds[category],
            "average_elapsed_ms": round(
                summary["elapsed_ms"] / summary["count"],
                1,
            ),
        }
    app.processEvents()
    return {
        "runtime": asdict(service.runtime),
        "sample_count": len(samples),
        "categories": categories,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=Path)
    args = parser.parse_args()
    if args.samples:
        args.samples.mkdir(parents=True, exist_ok=True)
        result = run_quality_suite(args.samples)
    else:
        with tempfile.TemporaryDirectory(prefix="aide-ocr-quality-") as directory:
            result = run_quality_suite(Path(directory))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if all(item["passed"] for item in result["categories"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
