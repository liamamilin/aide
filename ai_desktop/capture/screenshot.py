"""
截图捕获 —— 调用 macOS 原生 screencapture 进行框选截图

用于「截图快捷键 / 📎 菜单截图」：弹出系统级框选 UI，用户选区后落盘为 PNG，
返回临时文件路径（调用方负责复制到应用数据目录并清理）。

若屏幕录制权限未授予，screencapture 会输出
"could not create image from rect/display" 之类的错误 —— 本模块捕获并返回给调用方，
由上层引导用户去系统设置授权。
"""
import logging
import os
import subprocess
import tempfile
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def capture_region() -> Tuple[Optional[str], str]:
    """交互式框选截图。

    返回 (path, error)：
      - 成功：用户选中的 PNG 临时路径 + ""
      - 取消（ESC）：None + ""
      - 失败（如屏幕录制权限未授予）：None + 错误信息
    """
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    err = ""
    try:
        proc = subprocess.run(
            ["screencapture", "-i", "-x", path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            logger.warning("screencapture: %s", err)
    except Exception as e:
        err = str(e)
        logger.warning("screencapture failed: %s", e)

    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, ""

    try:
        os.remove(path)
    except OSError:
        pass
    return None, err


def is_permission_error(err: str) -> bool:
    """判断错误是否属于「屏幕录制权限未授予」"""
    return "could not create image" in err.lower()
