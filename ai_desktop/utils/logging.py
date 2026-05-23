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

    # 降低第三方库日志噪音
    logging.getLogger("pynput").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
