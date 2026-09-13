"""Validated, managed image storage and Ollama preparation."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS_PER_MESSAGE = 4
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_PIXELS = 40_000_000
MAX_INFERENCE_EDGE = 2048

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"}
_MANAGED_TOP_LEVEL = {"attachments", "images"}
_lock = threading.RLock()


class AttachmentError(OSError):
    """An image cannot safely enter the managed attachment lifecycle."""


@dataclass(frozen=True)
class ImageInfo:
    path: str
    original_name: str
    byte_size: int
    width: int
    height: int
    image_format: str
    mime_type: str
    sha256: str

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def needs_inference_resize(self) -> bool:
        return max(self.width, self.height) > MAX_INFERENCE_EDGE


def _app_support_dir() -> Path:
    data_override = os.environ.get("AIDE_DATA_DIR")
    if data_override:
        base = Path(data_override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "ai-desktop-assistant"
    else:
        base = Path.home() / ".local" / "share" / "ai-desktop-assistant"
    base.mkdir(parents=True, exist_ok=True)
    return base


def images_dir() -> Path:
    """Return the managed original-image directory."""
    directory = _app_support_dir() / "attachments" / "originals"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def inference_dir() -> Path:
    directory = _app_support_dir() / "attachments" / "inference"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


def _unique_name() -> str:
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mime_type(image_format: str) -> str:
    normalized = image_format.lower()
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized in {"tif", "tiff"}:
        return "image/tiff"
    return f"image/{normalized or 'unknown'}"


def inspect_image(path: str | Path, *, enforce_limits: bool = True) -> ImageInfo:
    """Decode an image and return trusted metadata.

    Reading the image, rather than accepting its suffix, rejects corrupt files and
    files whose extension does not match any decoder available in the packaged app.
    """
    from PyQt5.QtGui import QImageReader

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError("图片文件不存在，请重新选择。")
    byte_size = source.stat().st_size
    if byte_size <= 0:
        raise AttachmentError("图片文件为空，无法添加。")
    if enforce_limits and byte_size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("单张图片不能超过 20 MiB。")

    reader = QImageReader(str(source))
    reader.setAutoTransform(True)
    image_format = bytes(reader.format()).decode("ascii", errors="ignore").lower()
    image = reader.read()
    if image.isNull():
        detail = reader.errorString() or "无法解码"
        raise AttachmentError(f"图片已损坏或格式不受支持：{detail}")
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        raise AttachmentError("图片尺寸无效，无法添加。")
    if enforce_limits and width * height > MAX_ATTACHMENT_PIXELS:
        raise AttachmentError("单张图片不能超过 4000 万像素。")
    return ImageInfo(
        path=str(source.resolve()),
        original_name=source.name,
        byte_size=byte_size,
        width=width,
        height=height,
        image_format=image_format,
        mime_type=_mime_type(image_format),
        sha256=_sha256(source),
    )


def _atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def store_image(src_path: str) -> str:
    """Validate and atomically copy an original into managed storage."""
    source = Path(src_path).expanduser()
    info = inspect_image(source)
    suffix = source.suffix.lower() if source.suffix.lower() in _IMAGE_EXTS else ".png"
    destination = images_dir() / f"{_unique_name()}{suffix}"
    with _lock:
        _atomic_copy(source, destination)
        try:
            inspect_image(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    logger.info(
        "Stored attachment %s -> %s (%dx%d, %d bytes)",
        src_path,
        destination,
        info.width,
        info.height,
        info.byte_size,
    )
    return str(destination)


def store_pixmap(pixmap) -> str:
    """Validate and atomically persist a pasted or dropped QPixmap as PNG."""
    from PyQt5.QtGui import QPixmap

    if not isinstance(pixmap, QPixmap):
        raise TypeError("store_pixmap expects a QPixmap")
    if pixmap.isNull():
        raise AttachmentError("剪贴板图片为空，无法添加。")
    if pixmap.width() * pixmap.height() > MAX_ATTACHMENT_PIXELS:
        raise AttachmentError("单张图片不能超过 4000 万像素。")

    destination = images_dir() / f"{_unique_name()}.png"
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with _lock:
        try:
            if not pixmap.save(str(temporary), "PNG"):
                raise AttachmentError("无法保存剪贴板图片。")
            inspect_image(temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return str(destination)


def managed_relative_path(path: str | Path) -> str | None:
    """Return a safe app-data-relative path for managed or legacy images."""
    root = _app_support_dir().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in _MANAGED_TOP_LEVEL:
        return None
    return relative.as_posix()


def resolve_managed_path(relative_path: str) -> Path | None:
    root = _app_support_dir().resolve()
    try:
        candidate = (root / relative_path).resolve(strict=False)
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in _MANAGED_TOP_LEVEL:
        return None
    return candidate


def delete_managed_path(relative_path: str) -> bool:
    """Delete one safe managed file; never remove external user files."""
    target = resolve_managed_path(relative_path)
    if target is None:
        return False
    try:
        target.unlink(missing_ok=True)
        return True
    except OSError:
        logger.warning("Failed to delete managed attachment %s", target, exc_info=True)
        return False


def prepare_image_for_inference(path: str) -> str:
    """Return an image path suitable for Ollama, preserving the original.

    Oversized dimensions are decoded and resized in the calling worker thread.
    The resulting file is reusable and registered with the attachment record.
    """
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImageReader

    info = inspect_image(path)
    if not info.needs_inference_resize:
        return info.path

    reader = QImageReader(info.path)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise AttachmentError("图片读取失败，请重新添加图片。")
    scaled = image.scaled(
        MAX_INFERENCE_EDGE,
        MAX_INFERENCE_EDGE,
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    suffix = ".png" if scaled.hasAlphaChannel() else ".jpg"
    destination = inference_dir() / f"{Path(path).stem}-{info.sha256[:12]}{suffix}"
    if not destination.is_file():
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        image_format = "PNG" if suffix == ".png" else "JPEG"
        quality = -1 if suffix == ".png" else 88
        with _lock:
            try:
                if not scaled.save(str(temporary), image_format, quality):
                    raise AttachmentError("无法生成图片推理副本。")
                inspect_image(temporary, enforce_limits=False)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    try:
        from ai_desktop.utils import storage

        storage.set_attachment_inference_path(info.path, str(destination))
    except Exception:
        logger.warning("Could not register inference image %s", destination, exc_info=True)
    logger.info(
        "Prepared inference image %s -> %s (%dx%d -> %dx%d)",
        path,
        destination,
        info.width,
        info.height,
        scaled.width(),
        scaled.height(),
    )
    return str(destination)


def encode_image_base64(path: str) -> str:
    """Prepare and encode an image for Ollama without a data-URI prefix."""
    prepared = Path(prepare_image_for_inference(path))
    return base64.b64encode(prepared.read_bytes()).decode("ascii")


def discard_staged_image(path: str) -> bool:
    """Discard an unreferenced managed draft while preserving shared files."""
    from ai_desktop.utils import storage

    return storage.discard_staged_attachment(path)
