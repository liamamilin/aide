"""Load a spritesheet image and extract individual frames as QPixmaps."""

from pathlib import Path

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QImage, QPixmap


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
        *, remove_magenta_matte: bool = False,
    ) -> None:
        self._path = Path(image_path)
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._columns = columns
        self._frame_count = frame_count
        self._remove_matte = remove_magenta_matte
        self._source: QPixmap | None = None
        self._frames: list[QPixmap] = []

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

    def load(self) -> bool:
        """Load the spritesheet image and extract all frames. Returns True on success."""
        self._source = None
        self._frames = []
        if min(self._frame_width, self._frame_height, self._columns, self._frame_count) <= 0:
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
        for index in range(self._frame_count):
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
        return True

    def frame(self, index: int) -> QPixmap:
        """Return the QPixmap for the given frame index."""
        if 0 <= index < len(self._frames):
            return self._frames[index]
        return QPixmap()

    @staticmethod
    def from_manifest(manifest_dir: str | Path, file: str,
                      frame_width: int, frame_height: int,
                      columns: int, frame_count: int,
                      *, remove_magenta_matte: bool = False) -> "Spritesheet":
        """Create a Spritesheet with a path relative to the manifest directory."""
        full_path = Path(manifest_dir) / file
        return Spritesheet(
            image_path=full_path,
            frame_width=frame_width,
            frame_height=frame_height,
            columns=columns,
            frame_count=frame_count,
            remove_magenta_matte=remove_magenta_matte,
        )
