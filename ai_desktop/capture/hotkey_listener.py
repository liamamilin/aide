"""
全局快捷键监听模块 —— 支持多个快捷键
"""
import logging
from typing import Callable, Dict, Optional

from pynput import keyboard
from pynput.keyboard import HotKey

logger = logging.getLogger(__name__)


def validate_hotkey(hotkey: str) -> bool:
    """验证 pynput 快捷键格式是否可解析"""
    try:
        HotKey.parse(hotkey)
        return True
    except (ValueError, TypeError):
        return False


class HotkeyListener:
    """监听多组全局快捷键（pynput 自带独立线程）"""

    def __init__(self):
        self._mappings: Dict[str, Callable[[], None]] = {}
        self._listener: Optional[keyboard.GlobalHotKeys] = None

    def register(self, hotkey: str, callback: Callable[[], None]) -> None:
        """注册一个快捷键及其回调"""
        if not validate_hotkey(hotkey):
            raise ValueError(f"Invalid hotkey: {hotkey}")
        self._mappings[hotkey] = callback

    def start(self) -> None:
        if self._listener is not None:
            return
        if not self._mappings:
            logger.warning("No hotkeys registered, listener not started")
            return

        self._listener = keyboard.GlobalHotKeys(self._mappings)
        self._listener.start()
        logger.info("Hotkey listener started (%d hotkeys)", len(self._mappings))

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("Hotkey listener stopped")

    def reregister(self, new_hotkey: str, callback) -> None:
        """运行时更换快捷键（停止 → 清空 → 重新注册 → 重启）"""
        if not validate_hotkey(new_hotkey):
            raise ValueError(f"Invalid hotkey: {new_hotkey}")
        was_running = self._listener is not None
        self.stop()
        old_mappings = dict(self._mappings)
        self._mappings.clear()
        self._mappings[new_hotkey] = callback
        try:
            if was_running:
                self.start()
        except Exception:
            self._mappings = old_mappings
            if was_running:
                self.start()
            raise
