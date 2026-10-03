#!/usr/bin/env python3
"""Compile existing eye layers into a fixed-anchor atlas; no body redrawing.

Run: QT_QPA_PLATFORM=offscreen python3 -m ai_desktop.pets.owl-v2.generate_spritesheet
The same Qt composition path is used for all poses and transparent edges.
"""
import json
from pathlib import Path

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

from ai_desktop.ui.float_button import _visible_pet_bounds
from ai_desktop.ui.pet_layers import load_eye_expressions

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).parent
WIDTH, HEIGHT, COLUMNS = 312, 330, 8


def compile_atlas() -> None:
    base = QPixmap(str(ROOT / "桌面宠物-v2.png"))
    crop = _visible_pet_bounds(base)
    if base.isNull() or crop.isEmpty():
        raise RuntimeError("Missing master owl artwork")
    poses = {"open": base.copy(crop)}
    poses.update(load_eye_expressions(base, crop, str(ROOT / "pet_layers/master.json")))
    if not {"blink", "attentive", "focused"} <= poses.keys():
        raise RuntimeError("Missing eye expressions")
    # The body and feet keep one stable rectangle across every eye pose.
    ratio = min((WIDTH - 30) / crop.width(), (HEIGHT - 42) / crop.height())
    w, h = crop.width() * ratio, crop.height() * ratio
    rect = QRectF((WIDTH - w) / 2, HEIGHT - 30 - h, w, h)
    frames = []
    for name in ("open", "focused", "blink", "attentive"):
        frame = QPixmap(WIDTH, HEIGHT)
        frame.fill(Qt.transparent)
        p = QPainter(frame)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(rect, poses[name], QRectF(poses[name].rect()))
        p.end()
        frames.append(frame)
    # Acknowledgement has a short anticipation and smooth nod, never a sway.
    for offset in (0.5, 1.5, 2.5, 3.0, 2.5, 1.5, 0.5):
        frame = QPixmap(WIDTH, HEIGHT)
        frame.fill(Qt.transparent)
        p = QPainter(frame)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(rect.translated(0, offset), poses["attentive"], QRectF(poses["attentive"].rect()))
        p.end()
        frames.append(frame)
    rows = (len(frames) + COLUMNS - 1) // COLUMNS
    sheet = QPixmap(WIDTH * COLUMNS, HEIGHT * rows)
    sheet.fill(Qt.transparent)
    p = QPainter(sheet)
    for index, frame in enumerate(frames):
        p.drawPixmap((index % COLUMNS) * WIDTH, (index // COLUMNS) * HEIGHT, frame)
    p.end()
    if not sheet.save(str(OUTPUT / "spritesheet.png")):
        raise RuntimeError("Cannot save atlas")

    def clip(seq, times, *, loop=False, next_state=None):
        value = {"frames": seq, "durations_ms": times, "fps": 12, "loop": loop}
        if next_state:
            value["next_state"] = next_state
        return value

    animations = {
        "rest": clip([0], [1000], loop=True),
        "attention": clip([3], [1000], loop=True),
        "focus": clip([1], [1000], loop=True),
        "blink": clip([0, 1, 2, 1, 0], [50, 35, 95, 35, 80]),
        "double_blink": clip([0, 1, 2, 1, 0, 1, 2, 1, 0], [50, 35, 85, 35, 160, 35, 80, 35, 80]),
        "slow_blink": clip([0, 1, 2, 1, 0], [120, 100, 180, 100, 160]),
        "observe": clip([0, 3, 0], [100, 620, 140]),
        "rest_eyes": clip([0, 1, 0], [100, 420, 140]),
        "greet": clip([3, 1, 2, 1, 3], [160, 40, 100, 40, 260]),
        "nod": clip([3, 4, 5, 6, 7, 8, 9, 10, 3], [120, 45, 45, 45, 70, 45, 45, 45, 240]),
        "curious": clip([3, 1, 3], [240, 160, 360]),
        "friendly": clip([3, 1, 2, 1, 3], [240, 100, 180, 100, 320]),
        "hello_twice": clip([3, 2, 3, 2, 3], [120, 90, 170, 90, 260]),
        "success": clip(
            [3, 4, 5, 6, 7, 8, 9, 10, 3, 0],
            [100, 45, 45, 45, 70, 45, 45, 45, 240, 160], next_state="idle"),
        "error": clip([0, 1, 2, 1, 0], [120, 140, 200, 140, 300], next_state="idle"),
    }
    manifest = {
        "schema_version": 1, "name": "owl",
        "spritesheet": {"file": "spritesheet.png", "frame_width": WIDTH, "frame_height": HEIGHT,
                        "columns": COLUMNS, "frame_count": len(frames)},
        "anchor": {"x": 0.5, "y": 1.0},
        "defaults": {"state": "idle", "fps": 24},
        "states": {
            "idle": {"base": "rest", "ambient": ["blink", "double_blink", "slow_blink", "observe", "rest_eyes"]},
            "hover": {"base": "attention", "reactions": ["greet", "nod", "curious", "friendly", "hello_twice"]},
            "working": {"base": "focus"}, "speaking": {"base": "attention"}, "listening": {"base": "attention"},
            "success": {"base": "success"}, "error": {"base": "error"},
        },
        "animations": animations,
    }
    (OUTPUT / "pet.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    compile_atlas()
