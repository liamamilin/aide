"""Load a spritesheet image and extract individual frames as QPixmaps."""

import hashlib
import logging
from collections import OrderedDict
from pathlib import Path

from PyQt5.QtCore import QPointF, QRect, Qt
from PyQt5.QtGui import QImage, QImageReader, QPainter, QPainterPath, QPixmap

from ai_desktop.ui.pet_manifest import FramePatches, SecondaryMotion
from ai_desktop.ui.pet_secondary import compose_secondary

_logger = logging.getLogger(__name__)


def _remove_magenta_matte(source: QPixmap) -> QPixmap:
    """Despill the known magenta matte without erasing alpha or dark outlines.

    Only fingerprinted profiles opt into this decoder correction. Saturated
    blue feathers, red clothing and brown fur are outside the targeted range.
    """
    image = source.toImage().convertToFormat(QImage.Format_RGBA8888)
    pointer = image.bits()
    pointer.setsize(image.byteCount())
    pixels = bytearray(pointer)
    for offset in range(0, len(pixels), 4):
        if pixels[offset + 3] == 0:
            continue
        red, green, blue = pixels[offset:offset + 3]
        if (min(red, blue) >= 90 and min(red, blue) > green * 1.8
                and 0.72 * blue <= red <= 1.4 * blue):
            spill = min(red, blue) - green
            pixels[offset], pixels[offset + 2] = red - spill, blue - spill
    corrected = QImage(bytes(pixels), image.width(), image.height(), image.bytesPerLine(),
                       QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(corrected)


class Spritesheet:
    """Grid-based spritesheet: fixed-size cells, bottom-center anchor."""

    def __init__(
        self,
        image_path: str | Path,
        frame_width: int,
        frame_height: int,
        columns: int,
        frame_count: int,
        *, remove_magenta_matte: bool = False, frame_patches: FramePatches | None = None,
    ) -> None:
        self._path = Path(image_path)
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._columns = columns
        self._base_frame_count = frame_count
        self._frame_count = frame_count + (len(frame_patches.frames) if frame_patches else 0)
        self._patches = frame_patches
        self._patches_loaded = False
        self._remove_matte = remove_magenta_matte
        self._source: QPixmap | None = None
        self._frames: list[QPixmap] = []
        self._secondary_cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._secondary_cache_limit = min(24, max(1, 2_000_000 // max(1, frame_width * frame_height)))

    @property
    def frame_width(self) -> int:
        return self._frame_width

    @property
    def frame_height(self) -> int:
        return self._frame_height

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def is_loaded(self) -> bool:
        return self._source is not None and len(self._frames) == self._frame_count

    @property
    def patches_loaded(self) -> bool:
        return self._patches_loaded

    def load(self) -> bool:
        """Load the spritesheet image and extract all frames. Returns True on success."""
        self._source = None
        self._frames = []
        self._secondary_cache.clear()
        self._patches_loaded = False
        if min(self._frame_width, self._frame_height, self._columns, self._base_frame_count) <= 0:
            return False
        if not self._path.exists():
            return False
        source = QPixmap(str(self._path))
        if source.isNull():
            return False
        if self._remove_matte:
            source = _remove_magenta_matte(source)
        self._source = source
        self._frames = []
        for index in range(self._base_frame_count):
            col = index % self._columns
            row = index // self._columns
            x = col * self._frame_width
            y = row * self._frame_height
            if x + self._frame_width > source.width() or y + self._frame_height > source.height():
                self._frames.clear()
                self._source = None
                return False
            frame = source.copy(QRect(x, y, self._frame_width, self._frame_height))
            self._frames.append(frame)
        if self._patches:
            self._append_patches(self._patches)
        return True

    def _append_patches(self, patches: FramePatches) -> None:
        """Append local repainting over one immutable body, with safe source fallbacks."""
        source = QPixmap()
        try:
            expected_height = ((patches.frame_count + patches.columns - 1) // patches.columns) * self._frame_height
            reader = QImageReader(str(patches.image_path))
            size = reader.size()
            if size.width() != patches.columns * self._frame_width or size.height() != expected_height:
                raise ValueError("patch atlas dimensions do not match")
            if patches.sha256 and hashlib.sha256(patches.image_path.read_bytes()).hexdigest() != patches.sha256:
                raise ValueError("patch atlas fingerprint does not match")
            source = QPixmap.fromImage(reader.read())
            if source.isNull():
                raise ValueError("patch atlas cannot be decoded")
            self._patches_loaded = True
        except (OSError, ValueError) as exc:
            _logger.warning("Pet motion patch unavailable; using original poses: %s", exc)

        mask = QPainterPath(QPointF(*patches.mask[0]))
        for point in patches.mask[1:]:
            mask.lineTo(QPointF(*point))
        mask.closeSubpath()
        for item in patches.frames:
            if item.source_frame is not None:
                overlay = self._frames[item.source_frame]
            elif self._patches_loaded:
                overlay = source.copy(item.cell % patches.columns * self._frame_width,
                                      item.cell // patches.columns * self._frame_height,
                                      self._frame_width, self._frame_height)
            else:
                self._frames.append(self._frames[item.fallback_frame].copy())
                continue
            # Even a fully opaque custom atlas must be able to erase the old
            # limb. QPixmap.copy() may retain an RGB-only backing store.
            frame = QPixmap(self._frame_width, self._frame_height)
            frame.fill(Qt.transparent)
            painter = QPainter(frame)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.drawPixmap(0, 0, self._frames[patches.base_frame])
            # Hard clip ownership guarantees all pixels outside the authored
            # limb region remain byte-for-byte identical, including the face.
            painter.setClipPath(mask)
            painter.drawPixmap(0, 0, overlay)
            painter.end()
            self._frames.append(frame)

    def frame(self, index: int, *, secondary: SecondaryMotion | None = None,
              angles: tuple[float, ...] = (), pixel_art: bool = False) -> QPixmap:
        """Return the QPixmap for the given frame index."""
        if 0 <= index < len(self._frames):
            if secondary and index in secondary.frames and any(angles):
                key = (secondary.parts, index, angles, pixel_art)
                if key not in self._secondary_cache:
                    self._secondary_cache[key] = compose_secondary(self._frames[index], secondary, angles,
                                                                    pixel_art=pixel_art)
                    if len(self._secondary_cache) > self._secondary_cache_limit:
                        self._secondary_cache.popitem(last=False)
                self._secondary_cache.move_to_end(key)
                return self._secondary_cache[key]
            return self._frames[index]
        return QPixmap()

    @staticmethod
    def from_manifest(manifest_dir: str | Path, file: str,
                      frame_width: int, frame_height: int,
                      columns: int, frame_count: int,
                      *, remove_magenta_matte: bool = False,
                      frame_patches: FramePatches | None = None) -> "Spritesheet":
        """Create a Spritesheet with a path relative to the manifest directory."""
        full_path = Path(manifest_dir) / file
        return Spritesheet(
            image_path=full_path,
            frame_width=frame_width,
            frame_height=frame_height,
            columns=columns,
            frame_count=frame_count,
            remove_magenta_matte=remove_magenta_matte,
            frame_patches=frame_patches,
        )
