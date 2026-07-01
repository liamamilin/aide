import os
import sys


def resource_path(*parts: str) -> str:
    """返回资源绝对路径，兼容 PyInstaller 冻结和开发模式"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base: str = sys._MEIPASS  # type: ignore[arg-type]
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)
