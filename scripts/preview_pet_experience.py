#!/usr/bin/env python3
"""Audit four real pet assets and render the shared action surface.

QT_QPA_PLATFORM=offscreen python3 scripts/preview_pet_experience.py
QT_QPA_PLATFORM=cocoa python3 scripts/preview_pet_experience.py --native-smoke
No controller, model, clipboard capture, database or hotkeys are started.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLineEdit  # noqa: E402

from ai_desktop import config  # noqa: E402
from ai_desktop.ui.float_button import FloatButton, _get_pet_manifest_path  # noqa: E402

OUTPUT = ROOT / "docs/assets"
TAG = "2026-10-07"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pause(pet):
    for timer in (pet._track_timer, pet._animation_timer, pet._idle_wake_timer,
                  pet._toolbar_show_timer, pet._toolbar_hide_timer, pet._result_timer,
                  pet._hover_reaction_timer):
        timer.stop()
    pet._last_animation_time = time.monotonic()


def configure(pet, state):
    pet._hovered = True
    pet.set_responding(False)
    pet.set_speaking(False)
    pet._clear_result_state()
    pet.mark_result_unread(None)
    if state in {"searching", "waiting", "executing"}:
        pet.set_responding(True)
        pet.set_task_activity(state)
    elif state == "speaking":
        pet.set_speaking(True)
    elif state == "unread":
        pet.mark_result_unread("success")
    pet._show_toolbar()
    pet._animator.tick(.6)
    pet._update_spritesheet_frame()
    pause(pet)


def render_pose_audit(pet, name):
    indices = sorted({index for animation in pet._animator.manifest.animations.values()
                      for index in animation.frames})
    contact = QPixmap(8 * 160, ((len(indices) + 7) // 8) * 240)
    contact.fill(QColor("#f4f6fa"))
    painter = QPainter(contact)
    for slot, index in enumerate(indices):
        x, y = slot % 8 * 160, slot // 8 * 240
        frame = pet._spritesheet.frame(index)
        sampling = Qt.FastTransformation if pet._animator.manifest.rendering.pixel_art else Qt.SmoothTransformation
        painter.drawPixmap(x, y + 5, frame.scaled(160, 205, Qt.KeepAspectRatio, sampling))
        painter.setPen(QColor("#222222"))
        painter.drawText(QRect(x, y + 210, 160, 25), Qt.AlignCenter, str(index))
    painter.end()
    contact.save(str(OUTPUT / f"pet-pose-audit-{name}-{TAG}.png"))


def main():
    global TAG
    if "--tag" in sys.argv:
        TAG = sys.argv[sys.argv.index("--tag") + 1]
    native = "--native-smoke" in sys.argv
    app = QApplication.instance() or QApplication([])
    previous = config.PET_SOURCE, config.PET_NAME
    report = {"platform": app.platformName(), "pets": {}}
    OUTPUT.mkdir(exist_ok=True)
    editor = QLineEdit("pet selection fixture") if native else None
    try:
        for name in ("owl-v2", "astra", "boba", "shinchan"):
            config.PET_SOURCE, config.PET_NAME = ("built-in" if name == "owl-v2" else "petdex"), name
            manifest_path = Path(_get_pet_manifest_path(config.PET_SOURCE, name))
            raw = json.loads(manifest_path.read_text())
            atlas_path = manifest_path.parent / raw["spritesheet"]["file"]
            before = digest(manifest_path), digest(atlas_path)
            pet = FloatButton()
            try:
                pet.show()
                pet.set_follow_cursor_screen(False)
                pet.move(500, 350)
                app.processEvents()
                manifest = pet._animator.manifest
                expected = "owl" if name == "owl-v2" else name
                if manifest.name != expected or (name != "owl-v2" and not manifest.profile_id):
                    raise RuntimeError(f"{name}: requested character/profile not loaded")
                if manifest.frame_patches and not pet._spritesheet.patches_loaded:
                    raise RuntimeError(f"{name}: authored limb patches did not load")
                checks = 0
                if native:
                    editor.show()
                    editor.activateWindow()
                    editor.setFocus()
                    editor.selectAll()
                    app.processEvents()
                    focused = app.focusWidget()
                    if focused is not editor:
                        raise RuntimeError("native input fixture did not acquire focus")
                    configure(pet, "idle")
                    app.processEvents()
                    if (app.focusWidget() is not focused or app.activeWindow() is not editor
                            or editor.selectedText() != "pet selection fixture"):
                        raise RuntimeError(f"{name}: hover changed focus or selection")
                    calls = []
                    pet.read_selection_requested.connect(lambda: calls.append("read"))
                    QTest.mouseClick(pet._toolbar.buttons[2], Qt.LeftButton)
                    app.processEvents()
                    if calls != ["read"] or app.focusWidget() is not focused or app.activeWindow() is not editor:
                        raise RuntimeError(f"{name}: action changed focus or did not dispatch")
                    report["pets"][name] = {"profile": manifest.profile_id, "focus_preserved": True,
                                             "selection_preserved": True, "read_dispatched": True}
                else:
                    render_pose_audit(pet, name)
                    contact = QPixmap(1120, 6 * 240)
                    contact.fill(Qt.transparent)
                    painter = QPainter(contact)
                    painter.setFont(QFont("Helvetica Neue", 11))
                    for row, (size, dark) in enumerate((size, dark) for size in ("small", "medium", "large")
                                                       for dark in (False, True)):
                        pet.set_pet_size(size)
                        for col, state in enumerate(("idle", "searching", "waiting", "unread")):
                            configure(pet, state)
                            x, y = col * 280, row * 240
                            painter.fillRect(QRect(x, y, 280, 240), QColor("#181c27" if dark else "#f4f6fa"))
                            image = pet.grab().toImage()
                            for yy in range(image.height()):
                                for xx in range(image.width()):
                                    if (image.pixelColor(xx, yy).alpha() >= 24
                                            and not pet.mask().contains(QPoint(xx, yy))):
                                        raise RuntimeError(f"{name}/{size}/{state}: painted pixel outside hit region")
                            if pet.mask().contains(QPoint(0, 0)):
                                raise RuntimeError(f"{name}: transparent corner blocks clicks")
                            painter.drawPixmap(x + (280 - pet._toolbar.width()) // 2, y + 8, pet._toolbar.grab())
                            painter.drawPixmap(x + (280 - pet.width()) // 2, y + 78, pet.grab())
                            painter.setPen(QColor("#c3ccdc" if dark else "#4c5668"))
                            painter.drawText(QRect(x, y + 216, 280, 24), Qt.AlignCenter, f"{name} · {size} · {state}")
                            checks += 1
                    painter.end()
                    contact.save(str(OUTPUT / f"pet-experience-{name}-{TAG}.png"))
                    report["pets"][name] = {"profile": manifest.profile_id, "size_theme_state_checks": checks,
                                             "transparent_corners": True, "clipped_pixels": 0}
                if before != (digest(manifest_path), digest(atlas_path)):
                    raise RuntimeError(f"{name}: original files changed")
                report["pets"][name]["source_files_unchanged"] = True
            finally:
                pet.close()
                pet._motion_preference.close()
                app.processEvents()
    finally:
        config.PET_SOURCE, config.PET_NAME = previous
        if editor:
            editor.close()
    path = OUTPUT / (f"pet-native-{TAG}.json" if native else f"pet-experience-audit-{TAG}.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
