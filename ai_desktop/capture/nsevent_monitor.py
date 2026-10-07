"""
基于 NSEvent 全局/本地事件监听的快捷键检测

用 PyObjC 的 NSEvent 监听 API 在主线程接收键盘事件。全局监听接收其他应用的
事件，本地监听补上当前应用前台时的事件；无需后台线程，不触发 libdispatch 主队列断言。

优势（相比 pynput / CGEventTap）：
  - 运行在主线程，无 dispatch queue 断言崩溃
  - 事件驱动（非轮询），无丢帧
  - PyObjC（AppKit/objc）已通过 pynput 依赖打包进 .app

需要权限：全局监听需要辅助功能 + 输入监听；本地监听不需要系统权限。
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# AppKit / Foundation 常量
_NSKeyDown_MASK = 1 << 10          # NSEventMaskKeyDown
_NSEvent_MOD_CMD = 1 << 20         # NSEventModifierFlagCommand
_NSEvent_MOD_CTRL = 1 << 18        # NSEventModifierFlagControl
_NSEvent_MOD_ALT = 1 << 19         # NSEventModifierFlagOption
_NSEvent_MOD_SHIFT = 1 << 17       # NSEventModifierFlagShift
SHORTCUT_MODIFIERS = _NSEvent_MOD_CMD | _NSEvent_MOD_CTRL | _NSEvent_MOD_ALT | _NSEvent_MOD_SHIFT

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
    "7": 26, "8": 28, "9": 25,
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
    if not isinstance(hotkey, str) or not hotkey.strip():
        raise ValueError('Empty hotkey')
    flags = 0
    key_code = None
    for part in hotkey.lower().split('+'):
        token = part.strip()
        if token.startswith('<') and token.endswith('>'):
            token = token[1:-1]
            if token in _MOD_FLAGS:
                flag = _MOD_FLAGS[token]
                if flags & flag:
                    raise ValueError('Duplicate modifier')
                flags |= flag
                continue
        if token not in _KEY_CODE or key_code is not None:
            raise ValueError('Unsupported hotkey key')
        key_code = _KEY_CODE[token]
    if key_code is None:
        raise ValueError('Missing hotkey key')
    return key_code, flags


def validate_hotkey(hotkey: str) -> bool:
    """验证快捷键格式是否可解析"""
    try:
        _parse(hotkey)
        return True
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
        self._local_monitor: object = None  # 当前应用内事件监听句柄（防 GC）
        self._handler: object = None  # block 引用（防 GC）
        self._generation = 0
        self._global_generation = 0
        self._global_handler = None

    def register(self, hotkey: str, callback: Callable[[], None]) -> None:
        """注册快捷键和回调"""
        if not validate_hotkey(hotkey):
            raise ValueError(f"Invalid hotkey: {hotkey}")
        self._key_code, self._mod_flags = _parse(hotkey)
        self._callback = callback

    def start(self, *, local_only: bool = False) -> None:
        """安装本地与全局事件监听（主线程）"""
        if self._local_monitor is not None and (local_only or self._monitor is not None):
            return  # 本地与全局均已安装

        # 本地监听不需要输入监听权限；即使用户正在本应用窗口内，也应能触发快捷键。
        # 全局监听则需要输入监听权限，未授权时只跳过全局部分，避免把本地快捷键一并禁用。
        from ai_desktop.utils.permissions import check_all
        perm = None if local_only else check_all()

        try:
            from AppKit import NSEvent
        except ImportError as e:
            logger.error("AppKit not available (PyObjC): %s", e)
            return

        key_code = self._key_code
        mod_flags = self._mod_flags
        generation = self._generation

        def _dispatch(event):
            # Native callbacks already queued when a monitor is removed must
            # not invoke a newly registered callback or a stopped controller.
            if generation != self._generation:
                return event
            repeated = getattr(event, 'isARepeat', None)
            if repeated is not None and repeated():
                return event
            flags = event.modifierFlags()
            if (flags & SHORTCUT_MODIFIERS) == mod_flags and event.keyCode() == key_code:
                cb = self._callback
                if cb:
                    try:
                        cb()
                    except Exception:
                        logger.exception("Hotkey callback error")
            # 本地监听器必须返回事件，否则会阻断 Qt/AppKit 后续处理。
            return event

        self._handler = _dispatch
        if self._local_monitor is None:
            self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                _NSKeyDown_MASK, _dispatch
            )
            if self._local_monitor is None:
                logger.warning("NSEvent.addLocalMonitor returned None")

        if perm is not None and perm.all_granted:
            self._install_global()
        elif perm is not None:
            logger.warning(
                "NSEventMonitor global 未启动：权限不足 (AX=%s, IM=%s)，本地监听仍可用",
                perm.accessibility, perm.input_monitoring,
            )
        if self._local_monitor is not None or self._monitor is not None:
            logger.info(
                "NSEventMonitor installed: keyCode=%d modFlags=0x%x local=%s global=%s",
                key_code, mod_flags,
                self._local_monitor is not None, self._monitor is not None,
            )

    def _install_global(self) -> None:
        if self._monitor is not None:
            return
        from AppKit import NSEvent

        generation = self._global_generation
        dispatch = self._handler

        def handle(event):
            if generation == self._global_generation and dispatch is not None:
                dispatch(event)

        self._global_handler = handle
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(_NSKeyDown_MASK, handle)
        if self._monitor is None:
            logger.warning('Global hotkey monitor unavailable; will retry')
        else:
            logger.info('Global hotkey monitor installed: keyCode=%d modFlags=0x%x',
                        self._key_code, self._mod_flags)

    def _remove_global(self) -> None:
        # Invalidate queued global callbacks without retiring the local handler.
        self._global_generation += 1
        monitor, self._monitor = self._monitor, None
        self._global_handler = None
        if monitor is not None:
            from AppKit import NSEvent
            NSEvent.removeMonitor_(monitor)

    def sync_permissions(self, status, *, refresh: bool = False) -> bool:
        """Preserve local monitoring; reconcile global access or recover on wake."""
        self.start(local_only=True)
        if not status.all_granted or refresh:
            self._remove_global()
        if status.all_granted:
            self._install_global()
        return self._local_monitor is not None and (not status.all_granted or self._monitor is not None)

    def stop(self) -> None:
        """移除本地与全局事件监听"""
        self._generation += 1
        try:
            self._remove_global()
        except Exception as e:
            logger.warning('Failed to remove global NSEvent monitor: %s', e)
        if self._monitor is not None or self._local_monitor is not None:
            try:
                from AppKit import NSEvent
                if self._local_monitor is not None:
                    NSEvent.removeMonitor_(self._local_monitor)
            except Exception as e:
                logger.warning("Failed to remove NSEvent monitor: %s", e)
            self._monitor = None
            self._local_monitor = None
            self._handler = None
            logger.info("NSEventMonitor removed")

    def reregister(self, hotkey: str, callback: Callable[[], None]) -> None:
        """运行时更换快捷键"""
        if not validate_hotkey(hotkey):
            raise ValueError(f"Invalid hotkey: {hotkey}")
        was_running = self._monitor is not None or self._local_monitor is not None
        self.stop()
        self._key_code, self._mod_flags = _parse(hotkey)
        self._callback = callback
        if was_running:
            self.start()

    def set_callback(self, cb: Callable[[], None]) -> None:
        self._callback = cb
