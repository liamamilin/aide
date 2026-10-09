#!/usr/bin/env python3
"""Audit original part transforms and fixed body pixels on real installed pets."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop.ui.pet_manifest import load_manifest  # noqa: E402
from ai_desktop.ui.spritesheet import Spritesheet  # noqa: E402

OUTPUT = ROOT / "docs/assets"
TAG = "secondary-v5-2026-10-08"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rgba(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    pointer = image.constBits()
    pointer.setsize(image.byteCount())
    return bytes(pointer)


def components(pixels, width, height):
    remaining = {i for i in range(width * height) if pixels[i * 4 + 3] >= 32}
    large = 0
    while remaining:
        queue, count = [remaining.pop()], 0
        while queue:
            index = queue.pop()
            count += 1
            x, y = index % width, index // width
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    neighbor = ny * width + nx
                    if 0 <= nx < width and 0 <= ny < height and neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
        large += count >= 12
    return large


def main():
    app = QApplication.instance() or QApplication([])
    report = {}
    for name in ("owl-v2", "boba"):
        profile = ROOT / ("ai_desktop/pets/owl-v2/pet.json" if name == "owl-v2"
                          else "ai_desktop/pets/petdex-profiles/boba.json")
        manifest = load_manifest(profile)
        info, motion = manifest.spritesheet, manifest.secondary_motion
        directory = profile.parent if name == "owl-v2" else Path.home() / ".petdex/pets/boba"
        original = [directory / "pet.json", directory / info.file]
        before = [digest(path) for path in original]
        sheet = Spritesheet(original[1], info.frame_width, info.frame_height, info.columns, info.frame_count,
                            remove_magenta_matte=manifest.rendering.remove_magenta_matte,
                            frame_patches=manifest.frame_patches)
        assert sheet.load()
        bounds = [QRect(*part.bounds) for part in motion.parts]
        protected = ([QRect(0, 74, info.frame_width, info.frame_height - 74)] if name == "owl-v2"
                     else [QRect(0, 0, 192, 135), QRect(48, 100, 65, 80), QRect(45, 184, 94, 24)])
        factors, difference_counts = (-1, -.5, 0, .5, 1), []
        for index in motion.frames:
            base = rgba(sheet.frame(index))
            for factor in factors:
                angles = tuple(part.angle_deg * factor for part in motion.parts)
                current = rgba(sheet.frame(index, secondary=motion, angles=angles,
                                           pixel_art=manifest.rendering.pixel_art))
                differences = 0
                for pixel in range(info.frame_width * info.frame_height):
                    offset = pixel * 4
                    if base[offset:offset + 4] == current[offset:offset + 4]:
                        continue
                    x, y = pixel % info.frame_width, pixel // info.frame_width
                    if not any(rect.contains(x, y) for rect in bounds):
                        raise RuntimeError(f"{name}/{index}: changed pixels outside part bounds")
                    if any(rect.contains(x, y) for rect in protected):
                        raise RuntimeError(f"{name}/{index}: changed face, prop or foot pixels")
                    differences += 1
                if factor and differences < 20:
                    raise RuntimeError(f"{name}/{index}: missing part movement")
                if not factor and differences:
                    raise RuntimeError(f"{name}/{index}: neutral pose differs")
                if index == motion.frames[0]:
                    difference_counts.append(differences)
                    if components(base, info.frame_width, info.frame_height) != components(
                            current, info.frame_width, info.frame_height):
                        raise RuntimeError(f"{name}: detached part at {angles}")
        page = QPixmap(info.frame_width * len(factors), (info.frame_height + 30) * 2)
        painter = QPainter(page)
        for row, dark in enumerate((False, True)):
            y = row * (info.frame_height + 30)
            painter.fillRect(0, y, page.width(), info.frame_height + 30,
                             QColor("#181c27" if dark else "#f4f6fa"))
            painter.setPen(QColor("white" if dark else "#253047"))
            for col, factor in enumerate(factors):
                angles = tuple(part.angle_deg * factor for part in motion.parts)
                frame = sheet.frame(motion.frames[0], secondary=motion, angles=angles,
                                    pixel_art=manifest.rendering.pixel_art)
                painter.drawPixmap(col * info.frame_width, y, frame)
                painter.drawText(QRect(col * info.frame_width, y + info.frame_height, info.frame_width, 28),
                                 Qt.AlignCenter, str(angles))
        painter.end()
        page.save(str(OUTPUT / f"pet-secondary-poses-{name}-{TAG}.png"))
        if before != [digest(path) for path in original]:
            raise RuntimeError(f"{name}: source assets changed")
        report[name] = {"parts": [part.name for part in motion.parts], "frames_checked": len(motion.frames),
                        "poses_checked": len(motion.frames) * len(factors),
                        "changed_pixels_at_reference_pose": difference_counts,
                        "outside_part_bounds_unchanged": True, "face_prop_feet_unchanged": True,
                        "neutral_pose_exact": True, "parts_connected": True, "source_files_unchanged": True,
                        "source_sha256": before, "cache_entries": len(sheet._secondary_cache),
                        "cache_limit": sheet._secondary_cache_limit}
    (OUTPUT / f"pet-secondary-audit-{TAG}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    del app


if __name__ == "__main__":
    main()
