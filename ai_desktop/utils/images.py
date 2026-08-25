"""
图片工具 —— 持久化存储 + Ollama 编码 + 格式判断

- 用户发送的图片会复制到应用数据目录（随对话历史保存），返回稳定路径
- 发送给 Ollama 视觉模型时，按 API 要求编码为 base64 字符串
"""
import base64
import logging
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"}

_lock = threading.Lock()


def _app_support_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "ai-desktop-assistant"
    else:
        base = Path.home() / ".local" / "share" / "ai-desktop-assistant"
    base.mkdir(parents=True, exist_ok=True)
    return base


def images_dir() -> Path:
    d = _app_support_dir() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


def _unique_name() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def store_image(src_path: str) -> str:
    """复制图片到应用数据目录，返回稳定存储路径（绝对）。

    原文件（剪贴板临时文件/拖入文件）不会被修改或删除。
    """
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(src_path)
    ext = src.suffix.lower() or ".png"
    dest = images_dir() / f"{_unique_name()}{ext}"
    with _lock:
        shutil.copy2(str(src), str(dest))
    logger.info("Stored image %s -> %s", src_path, dest)
    return str(dest)


def store_pixmap(pixmap) -> str:
    """从 QPixmap（如剪贴板粘贴的图片）直接保存为 PNG，返回存储路径。"""
    from PyQt5.QtGui import QPixmap

    if not isinstance(pixmap, QPixmap):
        raise TypeError("store_pixmap expects a QPixmap")
    dest = images_dir() / f"{_unique_name()}.png"
    ok = pixmap.save(str(dest), "PNG")
    if not ok:
        raise OSError(f"Failed to save pixmap to {dest}")
    return str(dest)


def encode_image_base64(path: str) -> str:
    """读取图片文件，返回 Ollama /api/chat 所需的 base64 字符串（无 data URI 前缀）。"""
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")
