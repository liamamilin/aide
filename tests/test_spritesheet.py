"""Tests for spritesheet loading and frame extraction."""

from pathlib import Path

from PyQt5.QtGui import QColor, QImage

from ai_desktop.ui.spritesheet import Spritesheet


def test_matte_correction_preserves_alpha_and_character_palette(qtbot, tmp_path):
    image = QImage(5, 1, QImage.Format_RGBA8888)
    colors = [QColor(220, 12, 220, 80), QColor(102, 5, 98, 255),
              QColor(30, 100, 220, 255), QColor(220, 40, 25, 255), QColor(160, 100, 60, 255)]
    for index, color in enumerate(colors):
        image.setPixelColor(index, 0, color)
    path = tmp_path / "matte.png"
    image.save(str(path))
    original = Spritesheet(path, 5, 1, 1, 1)
    corrected = Spritesheet(path, 5, 1, 1, 1, remove_magenta_matte=True)
    assert original.load() and corrected.load()
    before, after = original.frame(0).toImage(), corrected.frame(0).toImage()
    assert before.pixelColor(0, 0).red() > 150
    assert after.pixelColor(0, 0).red() < 20
    assert after.pixelColor(1, 0).red() < 20
    for index in range(5):
        assert before.pixelColor(index, 0).alpha() == after.pixelColor(index, 0).alpha()
    for index in (2, 3, 4):
        assert before.pixelColor(index, 0) == after.pixelColor(index, 0)


def _create_test_spritesheet(
    tmp_path: Path,
    frame_width: int = 64,
    frame_height: int = 64,
    columns: int = 4,
    frame_count: int = 8,
) -> str:
    rows = (frame_count + columns - 1) // columns
    sheet_width = columns * frame_width
    sheet_height = rows * frame_height
    image = QImage(sheet_width, sheet_height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))

    for i in range(frame_count):
        col = i % columns
        row = i // columns
        x = col * frame_width
        y = row * frame_height
        color_value = int(255 * i / max(1, frame_count - 1))
        for px in range(x, x + frame_width):
            for py in range(y, y + frame_height):
                image.setPixelColor(px, py, QColor(color_value, color_value, color_value, 255))

    path = tmp_path / "test_spritesheet.png"
    image.save(str(path))
    return str(path)


class TestSpritesheet:
    def test_load_valid_spritesheet(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path)
        ss = Spritesheet(path, frame_width=64, frame_height=64, columns=4, frame_count=8)

        assert ss.load() is True
        assert ss.is_loaded is True
        assert ss.frame_count == 8
        assert ss.frame_width == 64
        assert ss.frame_height == 64

    def test_frame_returns_valid_pixmap(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path)
        ss = Spritesheet(path, frame_width=64, frame_height=64, columns=4, frame_count=8)
        ss.load()

        frame = ss.frame(0)
        assert not frame.isNull()
        assert frame.width() == 64
        assert frame.height() == 64

    def test_frame_returns_null_for_invalid_index(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path)
        ss = Spritesheet(path, frame_width=64, frame_height=64, columns=4, frame_count=8)
        ss.load()

        frame = ss.frame(100)
        assert frame.isNull()

    def test_load_fails_for_missing_file(self, qtbot, tmp_path):
        ss = Spritesheet(
            tmp_path / "nonexistent.png",
            frame_width=64,
            frame_height=64,
            columns=4,
            frame_count=8,
        )

        assert ss.load() is False
        assert ss.is_loaded is False

    def test_load_fails_when_frame_exceeds_bounds(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path, frame_count=4)
        ss = Spritesheet(path, frame_width=64, frame_height=64, columns=4, frame_count=100)

        assert ss.load() is False
        assert ss.is_loaded is False

    def test_from_manifest_resolves_relative_path(self, qtbot, tmp_path):
        _create_test_spritesheet(tmp_path)
        ss = Spritesheet.from_manifest(
            tmp_path,
            "test_spritesheet.png",
            frame_width=64,
            frame_height=64,
            columns=4,
            frame_count=8,
        )

        assert ss.load() is True
        assert ss.is_loaded is True

    def test_frames_have_correct_content(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path, frame_width=32, frame_height=32, columns=2, frame_count=4)
        ss = Spritesheet(path, frame_width=32, frame_height=32, columns=2, frame_count=4)
        ss.load()

        frame0 = ss.frame(0)
        frame3 = ss.frame(3)

        image0 = frame0.toImage()
        image3 = frame3.toImage()

        color0 = image0.pixelColor(16, 16)
        color3 = image3.pixelColor(16, 16)

        assert color0.red() < color3.red()

    def test_properties_before_load(self, qtbot, tmp_path):
        path = _create_test_spritesheet(tmp_path)
        ss = Spritesheet(path, frame_width=64, frame_height=64, columns=4, frame_count=8)

        assert ss.frame_width == 64
        assert ss.frame_height == 64
        assert ss.frame_count == 8
        assert ss.is_loaded is False
