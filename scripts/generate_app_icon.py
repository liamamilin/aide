#!/usr/bin/env python3
"""Package the vendored Tabler message-2 glyph as macOS application icons.

QT_QPA_PLATFORM=offscreen python3 scripts/generate_app_icon.py
"""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QByteArray, QRect, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PyQt5.QtSvg import QSvgRenderer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

ASSETS = ROOT / 'ai_desktop/assets'
SOURCE = ASSETS / 'tabler/message-2.svg'
OUTPUT = ROOT / 'ai_desktop'


def render_icon(size):
    """Keep the upstream paths intact; recolor and place on a standard tile."""
    renderer = QSvgRenderer(QByteArray(SOURCE.read_bytes().replace(b'currentColor', b'#ffffff')))
    if not renderer.isValid():
        raise ValueError('Invalid upstream SVG')
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 1024, size / 1024)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#0a84ff'))
    painter.drawRoundedRect(QRectF(64, 64, 896, 896), 208, 208)
    renderer.render(painter, QRectF(184, 184, 656, 656))
    painter.end()
    return image


def main():
    app = QApplication.instance() or QApplication([])
    if not render_icon(1024).save(str(OUTPUT / '图标-v2.png')):
        raise RuntimeError('Cannot write PNG icon')
    with tempfile.TemporaryDirectory(prefix='aide-app-icon-') as temp:
        iconset = Path(temp) / 'AppIcon.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for ratio in (1, 2):
                suffix = '@2x' if ratio == 2 else ''
                path = iconset / f'icon_{size}x{size}{suffix}.png'
                if not render_icon(size * ratio).save(str(path)):
                    raise RuntimeError(f'Cannot write {path}')
        subprocess.run(['iconutil', '-c', 'icns', '-o', str(OUTPUT / '图标-v2.icns'), str(iconset)], check=True)
    preview = QImage(760, 260, QImage.Format_ARGB32_Premultiplied)
    preview.fill(QColor('#f4f6fa'))
    painter = QPainter(preview)
    painter.setFont(QFont('Helvetica Neue', 11))
    for x, size in ((32, 160), (230, 128), (398, 64), (502, 32), (574, 26), (640, 18)):
        painter.drawImage(x, 38 + (160 - size) // 2, render_icon(size))
        painter.setPen(QColor('#4c5668'))
        painter.drawText(QRect(x - 10, 210, max(60, size + 20), 24), Qt.AlignCenter, f'{size}px')
    painter.end()
    path = ROOT / 'docs/assets/应用图标-Tabler-2026-10-03.png'
    if not preview.save(str(path)):
        raise RuntimeError('Cannot write preview')
    print(json.dumps({'source': 'Tabler message-2', 'svg_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                      'outputs': ['图标-v2.png', '图标-v2.icns'], 'preview': str(path)}, ensure_ascii=False))
    app.processEvents()


if __name__ == '__main__':
    main()
