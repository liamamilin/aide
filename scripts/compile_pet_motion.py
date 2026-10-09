#!/usr/bin/env python3
"""Register authored limb poses to the original atlas without replacing bodies.

QT_QPA_PLATFORM=offscreen python3 scripts/compile_pet_motion.py
Original Petdex files are read-only inputs. Generated sources and calibration
are versioned; the app consumes only the compact registered patch atlases.
"""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRegion  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop.ui.spritesheet import Spritesheet  # noqa: E402

PROFILE_ROOT = ROOT / "ai_desktop/pets/petdex-profiles"
SOURCE_ROOT = ROOT / "docs/assets/pet-motion-source"
WIDTH, HEIGHT = 192, 208


def polygon(points):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def match_pixel_palette(image, reference):
    """Keep original pixel-art palette and binary alpha; no blended silhouettes."""
    colors = Counter(reference.pixelColor(x, y).rgb()
                     for y in range(reference.height()) for x in range(reference.width())
                     if reference.pixelColor(x, y).alpha() >= 128)
    palette = [QColor(rgb) for rgb, _ in colors.most_common(64)]
    cache = {}
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() < 128:
                image.setPixelColor(x, y, QColor(0, 0, 0, 0))
                continue
            key = color.rgb()
            if key not in cache:
                cache[key] = min(palette, key=lambda p: (p.red() - color.red()) ** 2
                                 + (p.green() - color.green()) ** 2 + (p.blue() - color.blue()) ** 2)
            image.setPixelColor(x, y, cache[key])
    return image


def reveal_body(limb, original, spec):
    pose = original.frame(spec["base_frame"]).copy()
    painter = QPainter(pose)
    painter.setClipPath(polygon(spec["mask"]))
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.fillRect(pose.rect(), Qt.transparent)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    painter.save()
    painter.setClipPath(polygon(spec["body_reveal_mask"]), Qt.IntersectClip)
    painter.drawPixmap(*spec.get("body_reveal_offset", [0, 0]), original.frame(spec["endpoint"]))
    painter.restore()
    painter.drawPixmap(0, 0, limb)
    painter.end()
    return pose


def preserve_head(pose, original, spec):
    """Put the moving limb behind the original head, including translucent edges."""
    height = spec.get("preserve_head_height", 0)
    if not height:
        return pose
    base = original.frame(spec["base_frame"])
    image = base.toImage()
    protected = QRegion()
    for y in range(height):
        x = 0
        while x < WIDTH:
            if image.pixelColor(x, y).alpha() == 0:
                x += 1
                continue
            start = x
            while x < WIDTH and image.pixelColor(x, y).alpha() > 0:
                x += 1
            protected |= QRegion(QRect(start, y, x - start, 1))
    painter = QPainter(pose)
    painter.setClipRegion(protected)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawPixmap(0, 0, base)
    painter.end()
    return pose


def main():
    app = QApplication.instance() or QApplication([])
    calibration = json.loads((SOURCE_ROOT / "calibration-v1.json").read_text())
    for name, spec in calibration.items():
        profile_path = PROFILE_ROOT / f"{name}.json"
        profile = json.loads(profile_path.read_text())
        original_path = Path.home() / ".petdex/pets" / name / profile["spritesheet"]["file"]
        if hashlib.sha256(original_path.read_bytes()).hexdigest() != profile["atlas_sha256"]:
            raise RuntimeError(f"{name}: source atlas differs from curated mapping")
        original = Spritesheet(original_path, WIDTH, HEIGHT, 8, profile["spritesheet"]["frame_count"],
                               remove_magenta_matte=True)
        if not original.load():
            raise RuntimeError(f"{name}: source atlas cannot be loaded")
        raw = QPixmap(str(SOURCE_ROOT / spec["file"]))
        if raw.isNull() or raw.width() % 3:
            raise RuntimeError(f"{name}: expected a generated three-cell strip")
        count = 4 if "compiled_endpoint_mask" in spec else 3
        output = QPixmap(WIDTH * count, HEIGHT)
        output.fill(Qt.transparent)
        for cell, rect in enumerate(spec["registration"]):
            source = raw.copy(cell * raw.width() // 3, 0, raw.width() // 3, raw.height())
            if "source_masks" in spec:
                isolated = QPixmap(source.size())
                isolated.fill(Qt.transparent)
                painter = QPainter(isolated)
                painter.setClipPath(polygon(spec["source_masks"][cell]))
                painter.drawPixmap(0, 0, source)
                painter.end()
                source = isolated
            pose = QPixmap(WIDTH, HEIGHT)
            pose.fill(Qt.transparent)
            painter = QPainter(pose)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, not profile["rendering"]["pixel_art"])
            painter.drawPixmap(QRectF(*rect), source, QRectF(source.rect()))
            painter.end()
            if profile["rendering"]["pixel_art"]:
                image = pose.toImage().convertToFormat(QImage.Format_RGBA8888)
                pose = QPixmap.fromImage(match_pixel_palette(image, original.frame(spec["base_frame"]).toImage()))
            if "body_reveal_mask" in spec:
                pose = reveal_body(pose, original, spec)
            pose = preserve_head(pose, original, spec)
            painter = QPainter(output)
            painter.drawPixmap(cell * WIDTH, 0, pose)
            painter.end()
        if "compiled_endpoint_mask" in spec:
            limb = QPixmap(WIDTH, HEIGHT)
            limb.fill(Qt.transparent)
            painter = QPainter(limb)
            painter.setClipPath(polygon(spec["compiled_endpoint_mask"]))
            painter.drawPixmap(0, 0, original.frame(spec["endpoint"]))
            painter.end()
            painter = QPainter(output)
            painter.drawPixmap(3 * WIDTH, 0, preserve_head(reveal_body(limb, original, spec), original, spec))
            painter.end()
        destination = PROFILE_ROOT / "motion" / f"{name}-limb-v1.png"
        destination.parent.mkdir(exist_ok=True)
        if not output.save(str(destination)):
            raise RuntimeError(f"{name}: cannot save patch atlas")
        profile["frame_patches"] = {
            "file": f"motion/{destination.name}", "columns": count, "frame_count": count,
            "base_frame": spec["base_frame"], "mask": spec["mask"],
            "frames": ([{"cell": cell} for cell in range(4)] if count == 4
                       else [{"cell": cell} for cell in range(3)] + [{"source_frame": spec["endpoint"]}]),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
        print(f"{name}: three registered limb poses, original files unchanged")
    # Keep the QApplication alive while all QPixmaps are destroyed.
    del app


if __name__ == "__main__":
    main()
