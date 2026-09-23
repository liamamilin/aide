"""Compose eye expressions over one immutable pet body and shared anchor."""

import json
from pathlib import Path

from PyQt5.QtCore import QRect, QRectF
from PyQt5.QtGui import QPainter, QPainterPath, QPixmap


def load_eye_expressions(base: QPixmap, crop: QRect, manifest_path: str) -> dict[str, QPixmap]:
    """Return cropped poses; missing or incompatible layers fall back to the caller's base."""
    if base.isNull() or crop.isNull():
        return {}
    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest["version"] != 1 or manifest["canvas"] != [base.width(), base.height()]:
            return {}
        masks = manifest["eye_masks"]
        expressions = manifest["expressions"]
        if not isinstance(masks, list) or not isinstance(expressions, dict):
            return {}
        clip = QPainterPath()
        for mask in masks:
            x = float(mask["x"])
            y = float(mask["y"])
            width = float(mask["width"])
            height = float(mask["height"])
            radius = float(mask["radius"])
            if (
                x < 0 or y < 0 or width <= 0 or height <= 0 or radius < 0
                or x + width > base.width() or y + height > base.height()
            ):
                return {}
            clip.addRoundedRect(
                QRectF(x - crop.x(), y - crop.y(), width, height), radius, radius
            )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}

    result: dict[str, QPixmap] = {}
    for name in ("blink", "attentive", "focused"):
        relative = expressions.get(name)
        if not isinstance(relative, str):
            continue
        source = QPixmap(str(manifest_file.parent / relative))
        if source.isNull() or source.size() != base.size():
            continue
        pose = base.copy(crop)
        painter = QPainter(pose)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipPath(clip)
        painter.drawPixmap(-crop.x(), -crop.y(), source)
        painter.end()
        result[name] = pose
    return result
