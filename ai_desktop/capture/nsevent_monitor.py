"""
基于 NSEvent 全局事件监听的全局快捷键检测

用 PyObjC 的 NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ 在主线程
监听全局键盘事件。无需后台线程，不触发 libdispatch 主队列断言。

优势（相比 pynput / CGEventTap）：
  - 运行在主线程，无 dispatch queue 断言崩溃
  - 事件驱动（非轮询），无丢帧
  - PyObjC（AppKit/objc）已通过 pynput 依赖打包进 .app

需要权限：辅助功能 + 输入监听（NSEvent 全局监听需要两者）。
"""
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# AppKit / Foundation 常量
_NSKeyDown_MASK = 1 << 10          # NSEventMaskKeyDown
_NSEvent_MOD_CMD = 1 << 20         # NSEventModifierFlagCommand
_NSEvent_MOD_CTRL = 1 << 18        # NSEventModifierFlagControl
_NSEvent_MOD_ALT = 1 << 19         # NSEventModifierFlagOption
_NSEvent_MOD_SHIFT = 1 << 17       # NSEventModifierFlagShift

# 修饰键名 → NSEvent 修饰标志位
_MOD_FLAGS: dict[str, int] = {
    "cmd": _NSEvent_MOD_CMD,
    "command": _NSEvent_MOD_CMD,
    "ctrl": _NSEvent_MOD_CTRL,
    "control": _NSEvent_MOD_CTRL,
    "alt": _NSEvent_MOD_ALT,
    "option": _NSEvent_MOD_ALT,
    "shift": _NSEvent_MOD_SHIFT,
}

# 键名 → macOS virtual keyCode
_KEY_CODE: dict[str, int] = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31,
    "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9,
    "w": 13, "x": 7, "y": 16, "z": 6,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
    "7": 24, "8": 25, "9": 26,
    "`": 50, "-": 27, "=": 24, "[": 33, "]": 30, "\\": 42,
    ";": 41, "'": 39, ",": 43, ".": 47, "/": 44,
    "space": 49, "return": 36, "tab": 48, "enter": 36,
    "escape": 53, "backspace": 51, "delete": 117,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def _parse(hotkey: str) -> tuple[int, int]:
    """解析 pynput 格式快捷键 → (keyCode, modifierFlags)

    例: '<cmd>+<ctrl>+l' → (37, 0x140000)
    """
    s = hotkey.lower()
    flags = 0
    for m in re.finditer(r"<(.*?)>", s):
        t = m.group(1).strip()
        if t in _MOD_FLAGS:
            flags |= _MOD_FLAGS[t]
    without_tags = re.sub(r"<.*?>", "", s).strip()
    parts = [p.strip() for p in without_tags.split("+") if p.strip()]
    key_code = 0
    if parts:
        key_code = _KEY_CODE.get(parts[-1], 0)
    return key_code, flags


def validate_hotkey(hotkey: str) -> bool:
    """验证快捷键格式是否可解析"""
    try:
        kc, flags = _parse(hotkey)
        return kc != 0 or flags != 0
    except Exception:
        return False


class NSEventMonitor:
    """基于 NSEvent.addGlobalMonitorForEventsMatchingMask 的全局快捷键监听

    API 与 HotkeyListener 兼容：register / start / stop / reregister / set_callback。
    运行在主线程（Qt 事件循环即 NSApplication run loop），无后台线程。
    """

    def __init__(self):
        self._key_code: int = 0
        self._mod_flags: int = 0
        self._callback: Optional[Callable[[], None]] = None
        self._monitor: object = None  # NSEvent global monitor handle（防 GC）
        self._handler: object = None  # block 引用（防 GC）

    def register(self, hotkey: str, callback: Callable[[], None]) -> None:
        """注册快捷键和回调"""
        if not validate_hotkey(hotkey):
            raise ValueError(f"Invalid hotkey: {hotkey}")
        self._key_code, self._mod_flags = _parse(hotkey)
        self._callback = callback

    def start(self) -> None:
        """安装全局事件监听（主线程）"""
        if self._monitor is not None:
            return  # 已安装

        # 先检查权限，未授权则不安装（等 recheck 定时器授权后再启动）
        from ai_desktop.utils.permissions import check_all
        perm = check_all()
        if not perm.all_granted:
            logger.warning(
                "NSEventMonitor 未启动：权限不足 (AX=%s, IM=%s)",
                perm.accessibility, perm.input_monitoring,
            )
            return

        try:
            from AppKit import NSEvent
        except ImportError as e:
            logger.error("AppKit not available (PyObjC): %s", e)
            return

        key_code = self._key_code
        mod_flags = self._mod_flags

        def _handler(event):
            # 只在按键修饰标志完全匹配时触发
            flags = event.modifierFlags()
            if (flags & mod_flags) == mod_flags and event.keyCode() == key_code:
                cb = self._callback
                if cb:
                    try:
                        cb()
                    except Exception:
                        logger.exception("Hotkey callback error")

        self._handler = _handler
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _NSKeyDown_MASK, _handler
        )
        if self._monitor is None:
            logger.error("NSEvent.addGlobalMonitor returned None — 权限不足？")
        else:
            logger.info(
                "NSEventMonitor installed: keyCode=%d modFlags=0x%x",
                key_code, mod_flags,
            )

    def stop(self) -> None:
        """移除全局事件监听"""
        if self._monitor is not None:
            try:
                from AppKit import NSEvent
                NSEvent.removeMonitor_(self._monitor)
            except Exception as e:
                logger.warning("Failed to remove NSEvent monitor: %s", e)
            self._monitor = None
            self._handler = None
            logger.info("NSEventMonitor removed")

    def reregister(self, hotkey: str, callback: Callable[[], None]) -> None:
        """运行时更换快捷键"""
        if not validate_hotkey(hotkey):
            raise ValueError(f"Invalid hotkey: {hotkey}")
        was_running = self._monitor is not None
        self.stop()
        self._key_code, self._mod_flags = _parse(hotkey)
        self._callback = callback
        if was_running:
            self.start()

    def set_callback(self, cb: Callable[[], None]) -> None:
        self._callback = cb
