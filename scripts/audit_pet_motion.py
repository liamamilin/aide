#!/usr/bin/env python3
"""Check real limb patches against original pets and export aligned contact sheets."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QPointF, QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from ai_desktop.ui.pet_manifest import load_manifest  # noqa: E402
from ai_desktop.ui.spritesheet import Spritesheet  # noqa: E402

OUTPUT = ROOT / "docs/assets"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    app = QApplication.instance() or QApplication([])
    report = {}
    for name in ("astra", "boba"):
        manifest = load_manifest(ROOT / f"ai_desktop/pets/petdex-profiles/{name}.json")
        info, patches = manifest.spritesheet, manifest.frame_patches
        original_path = Path.home() / ".petdex/pets" / name / info.file
        original_manifest = original_path.parent / "pet.json"
        before = digest(original_path), digest(original_manifest)
        sheet = Spritesheet(original_path, info.frame_width, info.frame_height, info.columns, info.frame_count,
                            remove_magenta_matte=True, frame_patches=patches)
        if not sheet.load() or not sheet.patches_loaded:
            raise RuntimeError(f"{name}: authored patches did not load")
        mask = QImage(info.frame_width, info.frame_height, QImage.Format_RGBA8888)
        mask.fill(Qt.transparent)
        path = QPainterPath(QPointF(*patches.mask[0]))
        for point in patches.mask[1:]:
            path.lineTo(QPointF(*point))
        path.closeSubpath()
        painter = QPainter(mask)
        painter.setClipPath(path)
        painter.fillRect(mask.rect(), Qt.white)
        painter.end()
        base = sheet.frame(patches.base_frame).toImage()
        differences = []
        for index in range(info.frame_count, sheet.frame_count):
            current = sheet.frame(index).toImage()
            count = 0
            for y in range(info.frame_height):
                for x in range(info.frame_width):
                    changed = base.pixelColor(x, y) != current.pixelColor(x, y)
                    count += changed
                    if changed and mask.pixelColor(x, y).alpha() == 0:
                        raise RuntimeError(f"{name}/{index}: changed body outside owned limb region")
                    if changed and y < (119 if name == "astra" else 114) and base.pixelColor(x, y).alpha() > 0:
                        raise RuntimeError(f"{name}/{index}: changed original head pixels")
                    if changed and name == "boba" and 51 <= x <= 104 and 110 <= y <= 166:
                        raise RuntimeError(f"{name}/{index}: changed tea cup")
                    if changed and y >= 185:
                        raise RuntimeError(f"{name}/{index}: changed foot anchor")
            if count < 20:
                raise RuntimeError(f"{name}/{index}: authored pose is indistinguishable from base")
            differences.append(count)
        for left, right in zip(range(info.frame_count, sheet.frame_count - 1),
                               range(info.frame_count + 1, sheet.frame_count)):
            if sheet.frame(left).toImage() == sheet.frame(right).toImage():
                raise RuntimeError(f"{name}: duplicate in-between pose")
        contact = QPixmap(6 * 220, 2 * 260)
        painter = QPainter(contact)
        indices = [manifest.animations["rest"].frames[0], patches.base_frame,
                   *range(info.frame_count, sheet.frame_count)]
        for row, dark in enumerate((False, True)):
            painter.fillRect(0, row * 260, contact.width(), 260, QColor("#181c27" if dark else "#f4f6fa"))
            painter.setPen(QColor("#dddddd" if dark else "#253047"))
            for col, index in enumerate(indices):
                painter.drawPixmap(col * 220 + 14, row * 260 + 8, sheet.frame(index))
                painter.drawText(QRect(col * 220, row * 260 + 225, 220, 25), Qt.AlignCenter, str(index))
        painter.end()
        contact.save(str(OUTPUT / f"pet-motion-v3-poses-{name}-2026-10-07.png"))
        if before != (digest(original_path), digest(original_manifest)):
            raise RuntimeError(f"{name}: original files modified")
        report[name] = {"original_files_unchanged": True, "patches_loaded": True,
                        "patch_sha256": digest(patches.image_path), "total_frames": sheet.frame_count,
                        "appended_frames": len(patches.frames), "changed_pixels_per_pose": differences,
                        "body_outside_limb_unchanged": True, "head_unchanged": True,
                        "feet_unchanged": True, "tea_cup_unchanged": True if name == "boba" else None}
    (OUTPUT / "pet-motion-v3-audit-2026-10-07.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    del app


if __name__ == "__main__":
    main()
