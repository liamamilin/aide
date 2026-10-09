#!/usr/bin/env python3
"""Check native pet lifecycle with real timers and simulated extended idle.

QT_QPA_PLATFORM=cocoa python3 scripts/audit_pet_closeout.py
Uses installed pet art read-only. No controller, model, DB, capture or hotkeys.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop import config  # noqa: E402
from ai_desktop.ui.float_button import FloatButton, _get_pet_manifest_path  # noqa: E402
from ai_desktop.ui.pet_animator import PetAnimator  # noqa: E402


def source_hashes(name):
    source = "built-in" if name == "owl-v2" else "petdex"
    path = Path(_get_pet_manifest_path(source, name))
    atlas = path.parent / json.loads(path.read_text())["spritesheet"]["file"]
    return [hashlib.sha256(file.read_bytes()).hexdigest() for file in (path, atlas)]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def settle_hover(pet):
    pet.set_responding(False)
    pet.set_reduce_motion(False)
    pet._hovered = True
    pet._hover_reaction_timer.stop()
    pet._animator.set_state("hover", restart=True, animate=False)
    pet._last_animation_time = time.monotonic()
    pet._update_spritesheet_frame()
    pet._sync_animation_timer()


def timers_stopped(pet):
    return not pet._animation_timer.isActive() and not pet._idle_wake_timer.isActive()


def check_pixels(pet):
    pixmap = pet.grab()
    image, ratio = pixmap.toImage(), pixmap.devicePixelRatioF()
    require(not pet.mask().contains(QPoint(0, 0)), "transparent corner blocks clicks")
    require(all(image.pixelColor(x, y).alpha() < 24 or pet.mask().contains(QPoint(int(x / ratio), int(y / ratio)))
                for y in range(image.height()) for x in range(image.width())), "clipped pet pixel")


def drag_and_release(pet):
    origin = QPointF(pet.rect().center())
    for kind, offset in ((QEvent.MouseButtonPress, 0), (QEvent.MouseMove, 40),
                         (QEvent.MouseButtonRelease, 40)):
        local = origin + QPointF(offset, 0)
        event = QMouseEvent(kind, local, QPointF(pet.mapToGlobal(local.toPoint())),
                            Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                            Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton,
                            Qt.NoModifier)
        QApplication.sendEvent(pet, event)
        if kind != QEvent.MouseButtonRelease:
            require(timers_stopped(pet), "held pose kept animation awake")
    require(not pet._is_dragging and pet._animator.current_animation == "release", "drag did not release")


def cancel_press(pet):
    clicks = []
    def on_click():
        clicks.append(True)
    pet.clicked.connect(on_click)
    try:
        origin = QPointF(pet.rect().center())
        press = QMouseEvent(QEvent.MouseButtonPress, origin, QPointF(pet.mapToGlobal(origin.toPoint())),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(pet, press)
        pet.leaveEvent(QEvent(QEvent.Leave))
        outside = origin + QPointF(200, 200)
        release = QMouseEvent(QEvent.MouseButtonRelease, outside, QPointF(pet.mapToGlobal(outside.toPoint())),
                              Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(pet, release)
        require(not clicks and not pet.isDown() and pet._press_global is None, "outside press dispatched click")
        require(pet._animator.layer == "base" and pet._idle_wake_timer.isActive(), "cancel did not return to idle")
    finally:
        pet.clicked.disconnect(on_click)


def toolbar_attention(pet):
    settle_hover(pet)
    pet._show_toolbar()
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet._toolbar.enterEvent(QEvent(QEvent.Enter))
    QTest.qWait(600)
    require(pet._animator.current_state == "hover" and timers_stopped(pet), "toolbar lost quiet attention")
    pet._toolbar.leaveEvent(QEvent(QEvent.Leave))
    QTest.qWait(900)
    require(not pet._toolbar.isVisible() and pet._animator.current_state == "idle"
            and pet._animator.layer == "base" and pet._idle_wake_timer.isActive(), "toolbar departure did not settle")
    settle_hover(pet)
    pet._show_toolbar()
    pet.leaveEvent(QEvent(QEvent.Leave))
    pet._toolbar.enterEvent(QEvent(QEvent.Enter))
    pet._toolbar._activate(0)
    QTest.qWait(450)
    require(not pet._toolbar.isVisible() and pet._animator.current_state == "idle"
            and pet._animator.layer == "base", "toolbar action retained attention")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="2026-10-09")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    previous = config.PET_SOURCE, config.PET_NAME
    report = {"platform": app.platformName(), "physical_screens": len(app.screens()),
              "pointer_input": "synthetic QWidget events; system cursor unchanged", "pets": {}}
    pet = None
    try:
        for name in ("owl-v2", "astra", "boba", "shinchan"):
            before = source_hashes(name)
            config.PET_SOURCE, config.PET_NAME = ("built-in" if name == "owl-v2" else "petdex"), name
            if pet is None:
                pet = FloatButton()
                pet.set_follow_cursor_screen(False)
                # Test both animation and reduced mode inside this fixture only.
                report["system_reduce_motion"] = pet._motion_preference.reduced
                pet._motion_preference.reduced = False
                pet.show()
            else:
                pet.reload_pet()
            pet.move(500, 350)
            app.processEvents()
            manifest = pet._animator.manifest
            require(manifest.name == ("owl" if name == "owl-v2" else name), "wrong reloaded character")
            require(not pet._hover_reaction_timer.isActive(), "reload retained hover dwell")

            idle = PetAnimator(manifest, rng=random.Random(19))
            gestures, quiet_samples, early_actions, late_actions = set(), 0, 0, 0
            for index in range(18_000):
                previous_layer = idle.layer
                snap = idle.tick(.1)
                require(0 <= snap.frame_index < pet._spritesheet.frame_count, "invalid idle frame")
                quiet_samples += idle.layer == "base"
                if idle.layer == "ambient":
                    gestures.add(snap.animation_name)
                    if previous_layer != "ambient":
                        early_actions += index < 600
                        late_actions += index >= 17_400
            require(quiet_samples > 9_000 and len(gestures) >= 3, "idle lost quiet periods or variety")
            require(early_actions >= 3 and late_actions < early_actions, "long idle did not become quieter")

            toolbar_attention(pet)
            cancel_press(pet)

            settle_hover(pet)
            drag_and_release(pet)
            QTest.qWait(250)
            check_pixels(pet)
            QTest.qWait(1_150)
            require(pet._animator.layer == "base" and timers_stopped(pet), "release failed to sleep")
            require(not any(pet._animator.snapshot().secondary_angles), "part did not settle")

            pet._play_pointer_gesture("release")
            QTest.qWait(250)
            pet.set_responding(True)
            require(not any(pet._animator.snapshot().secondary_angles), "task retained part movement")
            pet.set_task_activity("waiting")
            QTest.qWait(650)
            require(pet._animator.current_state == "waiting" and timers_stopped(pet), "waiting kept waking")

            settle_hover(pet)
            pet._play_pointer_gesture("release")
            QTest.qWait(250)
            pet.set_reduce_motion(True)
            require(timers_stopped(pet) and not any(pet._animator.snapshot().secondary_angles),
                    "reduced mode retained movement")

            settle_hover(pet)
            pet._play_pointer_gesture("release")
            QTest.qWait(250)
            pet.hide()
            require(timers_stopped(pet) and not any(pet._animator.snapshot().secondary_angles),
                    "hidden pet retained movement")
            pet.show()
            settle_hover(pet)
            pet._play_pointer_gesture("release")
            QTest.qWait(250)
            pet.set_pet_enabled(False)
            require(timers_stopped(pet) and not any(pet._animator.snapshot().secondary_angles),
                    "compact mode retained movement")
            pet.set_pet_enabled(True)
            check_pixels(pet)
            require(before == source_hashes(name), "original source files changed")
            report["pets"][name] = {"profile": manifest.profile_id, "reload_correct": True,
                "simulated_idle_seconds": 1_800, "idle_gestures_seen": sorted(gestures),
                "first_minute_actions": early_actions, "last_minute_actions": late_actions,
                "toolbar_attention_preserved": True, "toolbar_action_settled": True,
                "outside_press_cancelled": True,
                "quiet_samples": quiet_samples, "real_timer_release_settled": True,
                "task_preempts_parts": True, "waiting_timer_stopped": True,
                "reduce_hide_compact_clear": True, "clipped_pixels": 0, "source_files_unchanged": True}
    finally:
        if pet:
            pet.close()
            pet._motion_preference.close()
            app.processEvents()
        config.PET_SOURCE, config.PET_NAME = previous
    output = ROOT / f"docs/assets/pet-closeout-native-{args.tag}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
