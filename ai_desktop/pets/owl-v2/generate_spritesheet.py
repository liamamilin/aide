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
from ai_desktop.ui.pet_layers import compose_eyelids, compose_gaze, load_eye_expressions
from ai_desktop.ui.pet_manifest import load_manifest

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
    source_poses = [poses[name] for name in ("open", "focused", "blink", "attentive")]
    source_poses += [compose_eyelids(poses[name], poses["blink"], crop, closure)
                     for name in ("open", "attentive", "focused") for closure in (.33, .66)]
    source_poses += [compose_gaze(poses["open"], crop, dx, dy)
                     for dx, dy in ((-10, 0), (10, 0), (0, -8), (0, 8),
                                    (-5, 0), (5, 0), (0, -4), (0, 4))]
    for pose in source_poses:
        frame = QPixmap(WIDTH, HEIGHT)
        frame.fill(Qt.transparent)
        p = QPainter(frame)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(rect, pose, QRectF(pose.rect()))
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
        "blink": clip([0, 4, 5, 2, 5, 4, 0], [80, 35, 35, 85, 35, 45, 100]),
        "double_blink": clip([0, 4, 5, 2, 5, 4, 0, 4, 5, 2, 5, 4, 0],
                             [80, 30, 30, 80, 30, 40, 170, 30, 30, 70, 30, 40, 100]),
        "slow_blink": clip([0, 4, 5, 2, 5, 4, 0], [120, 70, 90, 180, 80, 90, 160]),
        "observe": clip([0, 4, 5, 2, 7, 6, 3, 6, 7, 2, 5, 4, 0],
                        [80, 30, 30, 60, 30, 40, 620, 30, 30, 60, 30, 40, 100]),
        "rest_eyes": clip([0, 1, 0], [100, 420, 140]),
        "greet": clip([0, 4, 5, 2, 7, 6, 3], [80, 35, 35, 100, 40, 50, 260]),
        "acknowledge": clip([3, 6, 7, 2, 7, 6, 3], [160, 65, 80, 160, 75, 80, 240]),
        "curious": clip([3, 1, 3], [240, 160, 360]),
        "friendly": clip([3, 6, 7, 2, 7, 6, 3], [240, 70, 80, 180, 75, 80, 320]),
        "hello_twice": clip([3, 6, 7, 2, 7, 6, 3, 6, 7, 2, 7, 6, 3],
                            [120, 30, 30, 80, 30, 40, 170, 30, 30, 80, 30, 40, 260]),
        "success": clip(
            [3, 6, 7, 2, 5, 4, 0], [160, 60, 70, 120, 60, 70, 160], next_state="idle"),
        "error": clip([0, 4, 5, 2, 5, 4, 0], [120, 80, 100, 200, 80, 100, 300], next_state="idle"),
        "screen_check": clip([0, 4, 5, 2, 9, 8, 1, 9, 2, 7, 6, 3, 6, 7, 2, 5, 4, 0],
                             [80, 30, 30, 60, 30, 40, 260, 40, 60, 30, 40, 260, 30, 30, 60, 30, 40, 100]),
        "scan": clip([0, 14, 10, 14, 0, 15, 11, 15, 0], [120, 50, 280, 60, 180, 50, 280, 60, 120]),
        "press": clip([5], [100]),
        "grab": clip([3], [100]),
        "click": clip([5, 2, 7, 6, 3], [35, 60, 40, 50, 100]),
        "release": clip([3, 6, 7, 2, 7, 6, 3], [60, 35, 35, 70, 40, 50, 100]),
    }
    transitions = {}
    for target, seqs in {
        "focus": [[0, 4, 5, 2, 9, 8, 1], [3, 6, 7, 2, 9, 8, 1]],
        "attention": [[0, 4, 5, 2, 7, 6, 3], [1, 8, 9, 2, 7, 6, 3]],
        "rest": [[3, 6, 7, 2, 5, 4, 0], [1, 8, 9, 2, 5, 4, 0]],
    }.items():
        transitions[target] = []
        for index, seq in enumerate(seqs):
            name = f"to_{target}_{index}"
            animations[name] = clip(seq, [45, 30, 30, 55, 35, 45, 60])
            transitions[target].append(name)
    transitions["success"] = list(transitions["attention"])
    transitions["error"] = list(transitions["rest"])
    for direction, full, half in (("left", 10, 14), ("right", 11, 15), ("up", 12, 16), ("down", 13, 17)):
        animations[f"look_{direction}"] = clip([0, half, full, half, 0, 4, 5, 2, 7, 6, 3],
                                              [45, 45, 260, 55, 55, 30, 30, 55, 35, 45, 100])
        name = f"from_{direction}"
        animations[name] = clip([full, half, 0], [45, 45, 60])
        transitions["rest"].append(name)
        for target in ("focus", "attention"):
            bridge = f"from_{direction}_to_{target}"
            seq = [full, half] + animations[f"to_{target}_0"]["frames"]
            animations[bridge] = clip(seq, [35, 35] + [45, 30, 30, 55, 35, 45, 60])
            transitions[target].append(bridge)
    transitions["success"] = list(transitions["attention"])
    transitions["error"] = list(transitions["rest"])
    for name, animation in animations.items():
        if "blink" in name or name in {"acknowledge", "friendly", "hello_twice"}:
            animation["family"] = "blink"
        elif name in {"observe", "scan", "greet"}:
            animation["family"] = "observe"
        elif name in {"rest_eyes", "curious"}:
            animation["family"] = "focus"
    animations["screen_check"].update(family="signature", weight=.3, cooldown_ms=45000)
    manifest = {
        "schema_version": 1, "name": "owl",
        "spritesheet": {"file": "spritesheet.png", "frame_width": WIDTH, "frame_height": HEIGHT,
                        "columns": COLUMNS, "frame_count": len(frames)},
        "anchor": {"x": 0.5, "y": 1.0},
        "defaults": {"state": "idle", "fps": 24},
        "states": {
            "idle": {"base": "rest", "ambient": ["blink", "double_blink", "slow_blink", "observe",
                                                  "rest_eyes", "scan", "screen_check"]},
            "hover": {"base": "attention", "reactions": ["greet", "acknowledge", "curious", "friendly", "hello_twice"]},
            "working": {"base": "focus"}, "speaking": {"base": "attention"}, "listening": {"base": "attention"},
            "searching": {"base": "focus"}, "executing": {"base": "focus"},
            "waiting": {"base": "attention"}, "cancelled": {"base": "rest"},
            "success": {"base": "success"}, "error": {"base": "error"},
        },
        "animations": animations,
        "transitions": transitions,
        "interactions": {action: action for action in ("press", "grab", "click", "release",
                                                       "look_left", "look_right", "look_up", "look_down")},
    }
    manifest["interactions"]["signature"] = "screen_check"
    # The JSON is also an authored behaviour configuration. Recompiling the
    # same atlas must retain tuned timing, interactions and part calibration.
    manifest_file = OUTPUT / "pet.json"
    if manifest_file.exists():
        authored = json.loads(manifest_file.read_text(encoding="utf-8"))
        if authored.get("spritesheet") == manifest["spritesheet"]:
            load_manifest(manifest_file)
            manifest = authored
    (OUTPUT / "pet.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    compile_atlas()
