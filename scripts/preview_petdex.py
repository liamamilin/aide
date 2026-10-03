#!/usr/bin/env python3
"""Render installed Petdex pets through the real widget; never change their files.

QT_QPA_PLATFORM=offscreen python3 scripts/preview_petdex.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PyQt5.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop import config  # noqa: E402
from ai_desktop.ui.float_button import FloatButton, _visible_pet_bounds  # noqa: E402
from scripts.preview_pet import pause  # noqa: E402

OUTPUT = ROOT / 'docs/assets'


def render_preview():
    app = QApplication.instance() or QApplication([])
    previous = config.PET_SOURCE, config.PET_NAME
    audit = {}
    OUTPUT.mkdir(exist_ok=True)
    try:
        for name in ('astra', 'boba', 'shinchan'):
            config.PET_SOURCE, config.PET_NAME = 'petdex', name
            button = FloatButton()
            try:
                button.show()
                button.set_follow_cursor_screen(False)
                pause(button)
                manifest = button._animator.manifest
                if manifest.profile_id != f'petdex-{name}-v1':
                    raise RuntimeError(f'{name}: known local atlas/profile not loaded')
                indices = sorted({i for a in manifest.animations.values() for i in a.frames})
                bounds = {i: _visible_pet_bounds(button._spritesheet.frame(i)) for i in indices}
                if any(b.isEmpty() for b in bounds.values()):
                    raise RuntimeError(f'{name}: blank animation frame')
                poses = [('Rest', 'idle', manifest.animations['rest'].frames[0]),
                         ('Blink', 'idle', manifest.animations['idle_blink'].frames[1]),
                         ('Greeting', 'hover', manifest.animations[manifest.states['hover'].reactions[0]].frames[2]),
                         ('Working', 'working', manifest.animations['focus'].frames[0]),
                         ('Reading', 'speaking', manifest.animations['speak'].frames[0]),
                         ('Error', 'error', manifest.animations['error'].frames[1])]
                contact = QPixmap(6 * 160, 6 * 180)
                contact.fill(Qt.transparent)
                p = QPainter(contact)
                p.setFont(QFont('Helvetica Neue', 10))
                row = 0
                for size in ('small', 'medium', 'large'):
                    button.set_pet_size(size)
                    pause(button)
                    for theme, color in (('Light', '#f4f6fa'), ('Dark', '#181c27')):
                        for col, (label, state, index) in enumerate(poses):
                            x, y = col * 160, row * 180
                            p.fillRect(QRect(x, y, 160, 180), QColor(color))
                            button._responding = state == 'working'
                            button._speaking = state == 'speaking'
                            button._listening = False
                            button._result_state = state if state == 'error' else None
                            button._hovered = state == 'hover'
                            button._spritesheet_frame = button._spritesheet.frame(index)
                            button._sync_hit_mask()
                            # Confirm painted character pixels survive the shared silhouette mask.
                            if state in ('idle', 'hover'):
                                painted = QImage(button.size(), QImage.Format_ARGB32_Premultiplied)
                                painted.fill(Qt.transparent)
                                painter = QPainter(painted)
                                painter.setRenderHint(QPainter.SmoothPixmapTransform, not manifest.rendering.pixel_art)
                                painter.drawPixmap(button._spritesheet_draw_rect(), button._spritesheet_frame,
                                                   QRectF(button._spritesheet_frame.rect()))
                                painter.end()
                                for yy in range(painted.height()):
                                    for xx in range(painted.width()):
                                        if painted.pixelColor(xx, yy).alpha() > 24 and not button.mask().contains(
                                                QPoint(xx, yy)):
                                            raise RuntimeError(f'{name}: clipped silhouette at {size}')
                            p.drawPixmap(x + (160 - button.width()) // 2,
                                         y + 5 + (147 - button.height()) // 2, button.grab())
                            p.setPen(QColor('#4c5668' if theme == 'Light' else '#c3ccdc'))
                            p.drawText(QRect(x, y + 151, 160, 24), Qt.AlignCenter, f'{size} · {label}')
                        row += 1
                p.end()
                contact.save(str(OUTPUT / f'petdex-{name}-2026-10-03.png'))
                button.set_pet_size('medium')
                pause(button)
                button._responding = button._speaking = button._listening = False
                button._result_state = None
                button._sync_hit_mask()
                for group in ('idle', 'hover'):
                    button._hovered = group == 'hover'
                    choices = manifest.states[group].ambient if group == 'idle' else manifest.states[group].reactions
                    frames, durations = [], []
                    for action in choices:
                        animation = manifest.animations[action]
                        for index, duration in [(manifest.animations['rest'].frames[0], 1600)] + list(zip(
                                animation.frames, animation.durations_ms)):
                            button._spritesheet_frame = button._spritesheet.frame(index)
                            canvas = QPixmap(180, 170)
                            canvas.fill(QColor('#181c27'))
                            painter = QPainter(canvas)
                            painter.drawPixmap((180 - button.width()) // 2, 16, button.grab())
                            painter.setFont(QFont('Helvetica Neue', 10))
                            painter.setPen(QColor('#c3ccdc'))
                            painter.drawText(QRect(0, 136, 180, 24), Qt.AlignCenter, action.replace('_', ' '))
                            painter.end()
                            image = canvas.toImage().convertToFormat(QImage.Format_RGBA8888)
                            pointer = image.bits()
                            pointer.setsize(image.byteCount())
                            frames.append(Image.frombytes('RGBA', (image.width(), image.height()), bytes(pointer)))
                            durations.append(duration)
                    frames[0].save(OUTPUT / f'petdex-{name}-{group}-2026-10-03.gif', save_all=True,
                                   append_images=frames[1:], duration=durations, loop=0, disposal=2)
                audit[name] = {'profile_id': manifest.profile_id, 'selected_frames': indices,
                               'source_bounds': {str(i): [b.x(), b.y(), b.width(), b.height()]
                                                 for i, b in bounds.items()},
                               'atlas_sha256': hashlib.sha256(button._spritesheet._path.read_bytes()).hexdigest(),
                               'empty_frames': 0, 'sizes_checked': ['small', 'medium', 'large'],
                               'themes_checked': ['light', 'dark'], 'silhouette_clipping': 0}
            finally:
                button.close()
                button._motion_preference.close()
                app.processEvents()
    finally:
        config.PET_SOURCE, config.PET_NAME = previous
    (OUTPUT / 'petdex-audit-2026-10-03.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({name: {'profile': entry['profile_id'], 'empty_frames': entry['empty_frames'],
                              'clipping': entry['silhouette_clipping']} for name, entry in audit.items()}))


if __name__ == '__main__':
    render_preview()
