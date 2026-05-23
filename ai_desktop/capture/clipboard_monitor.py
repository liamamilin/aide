"""
选中文本读取模块

策略：
  1. pynput CGEvent 模拟 ⌘C（主方案，低延迟）
  2. osascript + System Events keystroke（回退方案，macOS 权限隔离场景）
  不依赖 Accessibility API（只对原生 Cocoa 应用有效）。
"""
import logging
import subprocess
import time
from typing import Optional

from pynput.keyboard import Controller, Key

from ai_desktop.capture import text_normalizer

logger = logging.getLogger(__name__)

_keyboard = Controller()


def _read_clipboard() -> str:
    """通过 pbpaste 读取剪贴板"""
    try:
        return subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return ""


def _write_clipboard(text: str) -> None:
    """通过 pbcopy 写入剪贴板"""
    try:
        subprocess.run(
            ["pbcopy"],
            input=text,
            text=True,
            timeout=2,
        )
    except Exception:
        pass


def _try_cmd_c_via_pynput() -> bool:
    """尝试通过 pynput Controller 模拟 ⌘C。成功返回 True。"""
    try:
        with _keyboard.pressed(Key.cmd):
            _keyboard.press("c")
            _keyboard.release("c")
        return True
    except Exception:
        logger.warning("pynput Cmd+C failed", exc_info=True)
        return False


def _try_cmd_c_via_osascript() -> bool:
    """通过 osascript (System Events) 向前台应用发送 ⌘C。成功返回 True。"""
    script = (
        'tell application "System Events" '
        'to tell (first process whose frontmost is true) '
        'to keystroke "c" using command down'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=3,
        )
        return True
    except Exception:
        logger.warning("osascript Cmd+C failed", exc_info=True)
        return False


def read_selection() -> Optional[str]:
    """
    读取当前选中文本。

    流程：
    1. 保存当前剪贴板
    2. 等待修饰键释放
    3. 模拟 ⌘C（pynput，失败则回退 osascript）
    4. 等待剪贴板更新
    5. 读取新剪贴板
    6. 恢复原剪贴板
    """
    # 1. 保存原剪贴板
    saved = _read_clipboard()

    # 2. 等待修饰键释放
    time.sleep(0.1)

    # 3. 模拟 ⌘C
    if not _try_cmd_c_via_pynput():
        if saved:
            _write_clipboard(saved)
        return None

    # 4. 等待剪贴板更新
    time.sleep(0.15)

    # 5. 读取新内容
    selected = _read_clipboard()

    # 6. 如果剪贴板未变化，尝试 osascript 回退
    if saved and selected.strip() == saved.strip():
        logger.info("pynput Cmd+C had no effect, trying osascript fallback...")
        if _try_cmd_c_via_osascript():
            time.sleep(0.15)
            selected = _read_clipboard()

    # 7. 恢复原剪贴板
    if saved:
        _write_clipboard(saved)

    # 8. 判断是否有效
    if not selected:
        logger.info("Clipboard empty after Cmd+C — no text captured")
        return None

    if saved and selected.strip() == saved.strip():
        logger.info("Clipboard unchanged after Cmd+C — no selection detected")
        return None

    logger.info("Captured %d chars via clipboard", len(selected))
    return text_normalizer.normalize(selected)
