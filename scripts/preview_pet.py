#!/usr/bin/env python3
"""Render the real pet widget on light/dark backgrounds and its timed idle loop.

QT_QPA_PLATFORM=offscreen python3 scripts/preview_pet.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PyQt5.QtCore import QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop.ui.float_button import FloatButton  # noqa: E402
from ai_desktop.ui.pet_manifest import load_manifest  # noqa: E402

OUTPUT = ROOT / "docs/assets"


def pause(button):
    for timer in (button._animation_timer, button._idle_wake_timer, button._track_timer, button._result_timer):
        timer.stop()


def render_preview():
    app = QApplication.instance() or QApplication([])
    button = FloatButton()
    button.show()
    button.set_follow_cursor_screen(False)
    pause(button)
    backgrounds = [("Light", "#f4f6fa"), ("Dark", "#181c27")]
    poses = [("Rest", "idle", 0), ("Blink", "idle", 2), ("Attention", "hover", 3),
             ("Working", "working", 1), ("Speaking", "speaking", 3), ("Done", "success", 3)]
    contact = QPixmap(6 * 180, 6 * 185)
    contact.fill(Qt.transparent)
    p = QPainter(contact)
    p.setFont(QFont("Helvetica Neue", 11))
    row = 0
    for size in ("small", "medium", "large"):
        button.set_pet_size(size)
        for theme, color in backgrounds:
            for col, (name, state, frame_index) in enumerate(poses):
                x, y = col * 180, row * 185
                p.fillRect(QRect(x, y, 180, 185), QColor(color))
                button._responding = state == "working"
                button._speaking = state == "speaking"
                button._listening = False
                button._result_state = "success" if state == "success" else None
                button._hovered = state == "hover"
                button._spritesheet_frame = button._spritesheet.frame(frame_index)
                button._sync_hit_mask()
                p.drawPixmap(x + (180 - button.width()) // 2, y + 12 + (147 - button.height()) // 2, button.grab())
                p.setPen(QColor("#4c5668" if theme == "Light" else "#c3ccdc"))
                p.drawText(QRect(x, y + 156, 180, 24), Qt.AlignCenter, f"{size} · {name}")
            row += 1
    p.end()
    OUTPUT.mkdir(exist_ok=True)
    contact.save(str(OUTPUT / "宠物-精灵引擎-2026-10-03.png"))
    manifest = load_manifest(ROOT / "ai_desktop/pets/owl-v2/pet.json")
    button.set_pet_size("medium")
    button._responding = button._speaking = button._listening = False
    button._result_state = None
    button._hovered = False
    button._sync_hit_mask()
    for group in ("idle", "hover"):
        names = manifest.states[group].ambient if group == "idle" else manifest.states[group].reactions
        gif_frames, durations = [], []
        for name in names:
            animation = manifest.animations[name]
            sequence = [(0 if group == "idle" else 3, 1600)] + list(zip(
                animation.frames, animation.durations_ms))
            for index, duration in sequence:
                button._spritesheet_frame = button._spritesheet.frame(index)
                canvas = QPixmap(180, 170)
                canvas.fill(QColor("#181c27"))
                painter = QPainter(canvas)
                painter.drawPixmap((180 - button.width()) // 2, 16, button.grab())
                painter.setFont(QFont("Helvetica Neue", 11))
                painter.setPen(QColor("#c3ccdc"))
                painter.drawText(QRect(0, 136, 180, 24), Qt.AlignCenter, name.replace("_", " "))
                painter.end()
                image = canvas.toImage().convertToFormat(17)  # QImage.Format_RGBA8888
                pointer = image.bits()
                pointer.setsize(image.byteCount())
                gif_frames.append(Image.frombytes("RGBA", (image.width(), image.height()), bytes(pointer)))
                durations.append(duration)
        gif_frames[0].save(OUTPUT / f"宠物-{group}-2026-10-03.gif", save_all=True,
                           append_images=gif_frames[1:], duration=durations, loop=0, disposal=2)
    button.close()
    app.processEvents()


if __name__ == "__main__":
    render_preview()
