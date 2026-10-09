"""Finite, delayed follow-through of original sprite parts; no body deformation."""

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QPainter, QPainterPath, QPixmap

from ai_desktop.ui.pet_manifest import SecondaryMotion


class SecondaryPlayer:
    def __init__(self, motion: SecondaryMotion | None):
        self.motion = motion
        self._elapsed: float | None = None
        self._strength = 0.0

    @property
    def active(self) -> bool:
        return self._elapsed is not None

    @property
    def next_wake_seconds(self) -> float | None:
        if not self.active:
            return None
        end = (self.motion.duration_ms + max(part.delay_ms for part in self.motion.parts)) / 1000
        return min(1 / self.motion.fps, max(0.0, end - self._elapsed))

    def trigger(self, animation: str, family: str = "") -> None:
        if self.motion is None or self.active:
            return
        strength = self.motion.triggers.get(animation, self.motion.triggers.get(family, 0.0))
        if strength:
            self._elapsed, self._strength = 0.0, strength

    def clear(self) -> None:
        self._elapsed, self._strength = None, 0.0

    def advance(self, seconds: float) -> None:
        if not self.active:
            return
        self._elapsed += seconds
        end = (self.motion.duration_ms + max(part.delay_ms for part in self.motion.parts)) / 1000
        if self._elapsed + 1e-9 >= end:
            self.clear()

    def angles(self, frame: int) -> tuple[float, ...]:
        if not self.active or frame not in self.motion.frames:
            return ()
        result = []
        for part in self.motion.parts:
            u = (self._elapsed * 1000 - part.delay_ms) / self.motion.duration_ms
            value = math.sin(3 * math.pi * u) * (1 - u) ** 2 if 0 < u < 1 else 0.0
            # A small finite pose set bounds cache use without scaling pixel art.
            result.append(round(part.angle_deg * self._strength * value * 2) / 2)
        return tuple(result)


def polygon(points) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def compose_secondary(source: QPixmap, motion: SecondaryMotion, angles: tuple[float, ...],
                      *, pixel_art: bool = False) -> QPixmap:
    """Move original distal parts behind their unchanged, overlapping roots.

    The source mask overlaps the cutout so the fixed body covers the rotated
    root. A final ownership clip keeps all pixels outside part bounds exact.
    """
    if (source.isNull() or len(angles) != len(motion.parts) or not any(angles)
            or any(not math.isfinite(angle) or abs(angle) > abs(part.angle_deg)
                   for part, angle in zip(motion.parts, angles))):
        return source
    fixed = QPixmap(source.size())
    fixed.fill(Qt.transparent)
    painter = QPainter(fixed)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    for part, angle in zip(motion.parts, angles):
        if angle:
            painter.setClipPath(polygon(part.cutout_mask))
            painter.fillRect(fixed.rect(), Qt.transparent)
    painter.end()
    moving = QPixmap(source.size())
    moving.fill(Qt.transparent)
    painter = QPainter(moving)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, not pixel_art)
    ownership = QPainterPath()
    for part, angle in zip(motion.parts, angles):
        if not angle:
            continue
        layer = QPixmap(source.size())
        layer.fill(Qt.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setClipPath(polygon(part.source_mask))
        layer_painter.drawPixmap(0, 0, source)
        layer_painter.end()
        painter.save()
        painter.translate(*part.pivot)
        painter.rotate(angle)
        painter.translate(-part.pivot[0], -part.pivot[1])
        painter.drawPixmap(0, 0, layer)
        painter.restore()
        ownership.addRect(QRectF(*part.bounds))
    painter.drawPixmap(0, 0, fixed)
    painter.end()
    result = QPixmap(source.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawPixmap(0, 0, source)
    painter.setClipPath(ownership)
    painter.drawPixmap(0, 0, moving)
    painter.end()
    return result
