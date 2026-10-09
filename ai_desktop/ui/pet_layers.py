"""Compose eye expressions over one immutable pet body and shared anchor."""

import json
from pathlib import Path

from PyQt5.QtCore import QRect, QRectF, Qt
from PyQt5.QtGui import QPainter, QPainterPath, QPen, QPixmap


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


def compose_eyelids(pose: QPixmap, closed: QPixmap, crop: QRect, closure: float) -> QPixmap:
    """Lower upper lids over unchanged irises, using the original face texture.

    Source coordinates belong to the built-in owl's master artwork. The pupils,
    head, laptop and feet are never scaled, translated or cross-faded.
    """
    result = pose.copy()
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.translate(-crop.x(), -crop.y())
    ink = pose.toImage().pixelColor(450 - crop.x(), 512 - crop.y())
    for mirrored, mask_x in ((False, 370), (True, 645)):
        def point(x, y):
            return (1209 - x if mirrored else x, y)

        start = point(392, 550 + 63 * closure)
        control = point(451, 526 + 101 * closure)
        end = point(528, 570 + 79 * closure)
        cover = QPainterPath()
        cover.moveTo(*point(365, 538))
        cover.lineTo(*point(575, 538))
        cover.lineTo(*point(575, end[1]))
        cover.lineTo(*end)
        cover.quadTo(*control, *start)
        cover.lineTo(*point(365, start[1]))
        cover.closeSubpath()
        painter.save()
        painter.setClipPath(cover)
        # The closed-eye source has clean textured face above its lash line.
        painter.drawPixmap(QRectF(mask_x, 538, 205, 145), closed,
                           QRectF(mask_x - crop.x(), 550 - crop.y(), 205, 28))
        painter.restore()
        lid = QPainterPath()
        lid.moveTo(*start)
        lid.quadTo(*control, *end)
        painter.setPen(QPen(ink, 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(lid)
    painter.end()
    return result


def compose_gaze(pose: QPixmap, crop: QRect, dx: float, dy: float = 0) -> QPixmap:
    """Move the original textured pupils inside fixed eye silhouettes.

    Only the iris interior is repainted; the head, eye outline and anchor stay
    identical. Offsets are small master-art pixels, before atlas downsampling.
    """
    result = pose.copy()
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.translate(-crop.x(), -crop.y())
    for mirrored in (False, True):
        def point(x, y):
            return (1209 - x if mirrored else x, y)

        inside = QPainterPath()
        inside.moveTo(*point(405, 568))
        inside.cubicTo(*point(432, 556), *point(454, 560), *point(471, 575))
        inside.cubicTo(*point(494, 594), *point(506, 627), *point(509, 660))
        inside.cubicTo(*point(479, 669), *point(453, 669), *point(429, 658))
        inside.cubicTo(*point(409, 647), *point(394, 603), *point(405, 568))
        pupil = QPainterPath()
        pupil.moveTo(*point(470, 573))
        pupil.cubicTo(*point(497, 577), *point(510, 622), *point(510, 663))
        pupil.cubicTo(*point(484, 675), *point(454, 675), *point(442, 653))
        pupil.cubicTo(*point(429, 632), *point(435, 588), *point(456, 577))
        pupil.closeSubpath()
        painter.save()
        painter.setClipPath(inside)
        # Borrow the clear turquoise part of the same eye as a texture plate.
        plate_x = 781 if mirrored else 409
        painter.drawPixmap(inside.boundingRect(), pose,
                           QRectF(plate_x - crop.x(), 580 - crop.y(), 18, 42))
        painter.translate(dx, dy)
        painter.setClipPath(pupil, Qt.IntersectClip)
        painter.drawPixmap(crop.x(), crop.y(), pose)
        painter.restore()
    painter.end()
    return result
