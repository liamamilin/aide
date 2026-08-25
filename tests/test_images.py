"""图片工具测试 —— store / encode / 格式判断"""
import base64
from pathlib import Path

import pytest

from ai_desktop.utils import images as image_utils

# 1x1 透明 PNG
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def tmp_images_dir(tmp_path, monkeypatch):
    """让图片存储指向临时目录，避免污染真实应用数据目录"""
    monkeypatch.setattr(image_utils, "images_dir", lambda: tmp_path)
    return tmp_path


def _make_png(tmp_path: Path) -> Path:
    p = tmp_path / "sample.png"
    p.write_bytes(_PNG_BYTES)
    return p


class TestImageUtils:
    def test_is_image_file(self):
        assert image_utils.is_image_file("a.png")
        assert image_utils.is_image_file("/x/y/IMG_123.JPG")
        assert not image_utils.is_image_file("notes.txt")
        assert not image_utils.is_image_file("archive.tar.gz")

    def test_store_image_copies_to_images_dir(self, tmp_images_dir, tmp_path):
        src = _make_png(tmp_path)
        stored = image_utils.store_image(str(src))
        path = Path(stored)
        assert path.parent == tmp_images_dir  # images_dir 指向临时目录
        assert path.exists()
        assert path.read_bytes() == _PNG_BYTES
        # 源文件不受影响
        assert src.read_bytes() == _PNG_BYTES

    def test_store_image_missing_raises(self, tmp_images_dir):
        with pytest.raises(FileNotFoundError):
            image_utils.store_image("/nonexistent/xxx.png")

    def test_encode_image_base64(self, tmp_path):
        p = _make_png(tmp_path)
        encoded = image_utils.encode_image_base64(str(p))
        assert encoded == base64.b64encode(_PNG_BYTES).decode("ascii")
        assert "data:image" not in encoded  # 无 data URI 前缀

    def test_store_pixmap(self, qapp, tmp_images_dir):
        from PyQt5.QtGui import QColor, QPixmap

        pm = QPixmap(16, 16)
        pm.fill(QColor("red"))
        stored = image_utils.store_pixmap(pm)
        path = Path(stored)
        assert path.exists()
        assert path.suffix == ".png"
        saved = QPixmap(str(path))
        assert not saved.isNull()
