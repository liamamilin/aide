#!/usr/bin/env python3
"""Sample the real Qt player and widgets into a motion contact sheet and GIF.

QT_QPA_PLATFORM=offscreen python3 scripts/preview_pet_animation.py
No controller, model, database, capture or hotkeys are started.
"""

import hashlib
import io
import json
import random
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from preview_pet_experience import pause  # noqa: E402
from PyQt5.QtCore import QBuffer, QEvent, QIODevice, QPoint, QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop import config  # noqa: E402
from ai_desktop.ui.float_button import FloatButton, _get_pet_manifest_path  # noqa: E402
from ai_desktop.ui.pet_animator import PetAnimator  # noqa: E402

OUTPUT = ROOT / "docs/assets"
STEP, COUNT = .05, 90
TAG = "2026-10-07"
FEEL = False


def pillow(pixmap):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")


def source_hashes(name):
    path = Path(_get_pet_manifest_path("built-in" if name == "owl-v2" else "petdex", name))
    atlas = path.parent / json.loads(path.read_text())["spritesheet"]["file"]
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, atlas)]


def sample(pet, scenario):
    with patch("ai_desktop.ui.float_button.time.monotonic") as clock:
        return sample_with_clock(pet, scenario, clock)


def sample_with_clock(pet, scenario, clock):
    clock.return_value = 1000.0
    pet._last_animation_time = clock.return_value
    pet._hovered = False
    pet._last_hover_reaction_time = float("-inf")
    pet.set_responding(False)
    pet._clear_result_state()
    pet._animator = PetAnimator(pet._animator.manifest, rng=random.Random(7))
    pet._update_spritesheet_frame()
    pause(pet)
    frames, labels, secondary_poses = [], [], []
    for index in range(COUNT):
        clock.return_value = 1000 + index * STEP
        # Only the explicit STEP advances the player in deterministic previews.
        pet._last_animation_time = time.monotonic()
        if scenario == "task":
            if index == 0:
                pet.set_responding(True)
            elif index == 18:
                pet.set_task_activity("waiting")
            elif index == 36:
                pet.set_task_activity("executing")
            elif index == 54:
                pet.set_responding(False)
                pet.show_result(True)
        elif scenario == "signature" and index == 10:
            pet._play_pointer_gesture("signature")
        elif scenario == "pointer":
            if index == 6:
                pet._play_pointer_gesture("press", hold=True)
            elif index == 12:
                pet._play_pointer_gesture("grab", hold=True)
            elif index == 24:
                pet._play_pointer_gesture("release")
        elif scenario == "hover":
            if index in {4, 12, 26}:
                pet.enterEvent(QEvent(QEvent.Enter))
                pet._hover_entry_direction = "right"
            elif index in {5, 22, 42}:
                pet.leaveEvent(QEvent(QEvent.Leave))
            elif index in {15, 29}:
                # Sample the dwell callback at 150 ms; real QTimer dispatch
                # is separately covered by the Qt interaction tests.
                pet._react_to_hover()
        pause(pet)
        pet._update_spritesheet_frame()
        pet._animation_phase = int(index * STEP / .18) % 96
        frame = pet.grab()
        image = frame.toImage()
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() >= 24 and not pet.mask().contains(QPoint(x, y)):
                    raise RuntimeError(f"{pet._animator.manifest.name}/{scenario}/{index}: clipped pixel")
        frames.append(frame)
        secondary_poses.append(pet._animator.snapshot().secondary_angles)
        labels.append(pet._effective_state() if scenario == "task" else pet._animator.current_animation)
        pet._animator.tick(STEP)
    if scenario == "hover":
        rest = pet._animator.manifest.states["idle"].base
        if labels[5] != rest or labels[-1] != rest:
            raise RuntimeError(f"{pet._animator.manifest.name}: passing/finished hover did not settle")
    return frames, labels, {"secondary_samples": sum(any(pose) for pose in secondary_poses),
                           "secondary_poses": len({pose for pose in secondary_poses if any(pose)})}


def main():
    global TAG, FEEL
    if "--tag" in sys.argv:
        TAG = sys.argv[sys.argv.index("--tag") + 1]
    FEEL = "--feel" in sys.argv
    app = QApplication.instance() or QApplication([])
    previous = config.PET_SOURCE, config.PET_NAME
    report = {"platform": app.platformName(), "sample_step_ms": 50, "pets": {}}
    try:
        for name in ("owl-v2", "astra", "boba", "shinchan"):
            config.PET_SOURCE, config.PET_NAME = ("built-in" if name == "owl-v2" else "petdex"), name
            before = source_hashes(name)
            pet = FloatButton()
            try:
                pet.set_pet_size("medium")
                pet.set_follow_cursor_screen(False)
                pet.move(500, 350)
                pet.show()
                app.processEvents()
                if pet._animator.manifest.name != ("owl" if name == "owl-v2" else name):
                    raise RuntimeError(f"{name}: wrong character loaded")
                if pet._animator.manifest.frame_patches and not pet._spritesheet.patches_loaded:
                    raise RuntimeError(f"{name}: authored limb patches did not load")
                scenarios = ["task", "signature", "pointer"] + (["hover"] if FEEL else [])
                samples = [sample(pet, scenario) for scenario in scenarios]
                gif_frames = []
                for index in range(COUNT):
                    page = QPixmap(220 * len(samples), 260)
                    page.fill(QColor("#f4f6fa"))
                    painter = QPainter(page)
                    painter.setFont(QFont("PingFang SC", 13))
                    painter.setPen(QColor("#253047"))
                    for col, (frames, labels, _) in enumerate(samples):
                        x = col * 220
                        painter.drawText(QRect(x, 8, 220, 30), Qt.AlignCenter,
                                         ("状态衔接", "代表动作", "按下 · 抓起 · 放下", "掠过 · 停留 · 离开")[col])
                        pix = frames[index]
                        painter.drawPixmap(x + (220 - pix.width()) // 2, 65, pix)
                        painter.drawText(QRect(x, 211, 220, 25), Qt.AlignCenter, labels[index])
                    painter.end()
                    gif_frames.append(pillow(page))
                gif_path = OUTPUT / f"pet-animation-{name}-{TAG}.gif"
                gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:],
                                   duration=50, loop=0, optimize=False, disposal=2)
                # Static evidence at evenly spaced points, retaining actual widget size.
                contact = QPixmap(12 * 160, len(samples) * 220)
                contact.fill(QColor("#f4f6fa"))
                painter = QPainter(contact)
                painter.setFont(QFont("Helvetica Neue", 10))
                painter.setPen(QColor("#253047"))
                for row, (frames, labels, _) in enumerate(samples):
                    for col, index in enumerate((0, 2, 4, 6, 12, 18, 24, 36, 40, 54, 60, 80)):
                        pix = frames[index]
                        x, y = col * 160, row * 220
                        painter.drawPixmap(x + (160 - pix.width()) // 2, y + 15, pix)
                        painter.drawText(QRect(x, y + 165, 160, 20), Qt.AlignCenter, f"{index * STEP:.2f}s")
                        painter.drawText(QRect(x, y + 187, 160, 20), Qt.AlignCenter, labels[index])
                painter.end()
                contact.save(str(OUTPUT / f"pet-animation-strip-{name}-{TAG}.png"))
                manifest = pet._animator.manifest
                report["pets"][name] = {"profile": manifest.profile_id,
                                         "sampled_frames": COUNT * len(samples), "clipped_pixels": 0,
                                         "scenarios": scenarios,
                                         "hover_dwell_ms": pet._hover_reaction_timer.interval(),
                                         "variable_gestures": sum(a.tempo_variation > 0
                                                                  for a in manifest.animations.values()),
                                         "idle_choices": len(manifest.states["idle"].ambient),
                                         "signature": manifest.interactions["signature"],
                                         "patches_loaded": pet._spritesheet.patches_loaded,
                                         "total_frames": pet._spritesheet.frame_count,
                                         "secondary_parts": ([part.name for part in manifest.secondary_motion.parts]
                                                             if manifest.secondary_motion else []),
                                         "secondary_sampling": {scenario: samples[i][2]
                                                                for i, scenario in enumerate(scenarios)},
                                         "hover_trace": ({str(i): samples[3][1][i]
                                                          for i in (4, 5, 12, 15, 22, 29, 42, 60, 89)}
                                                         if FEEL else {}),
                                         "original_files_unchanged": before == source_hashes(name)}
                if before != source_hashes(name):
                    raise RuntimeError(f"{name}: original assets modified")
                print(f"{name}: {COUNT * len(samples)} frames checked, GIF exported")
            finally:
                pet.close()
                pet._motion_preference.close()
                app.processEvents()
    finally:
        config.PET_SOURCE, config.PET_NAME = previous
    (OUTPUT / f"pet-animation-audit-{TAG}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
