"""
日志模块
"""
import logging
import sys
from pathlib import Path


def setup(level: int = logging.INFO) -> None:
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # 文件日志（macOS：~/Library/Logs/ai-desktop-assistant/app.log）
    # 在 .app 无窗口模式下 stderr 被吞掉，文件日志是唯一诊断手段
    if sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / "ai-desktop-assistant"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(log_dir / "app.log"), encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            pass

    # 降低第三方库日志噪音
    logging.getLogger("pynput").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
