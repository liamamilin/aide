"""
macOS 权限检测与请求 —— 辅助功能 + 输入监听

检测（check_*）：用 ctypes 直接加载系统框架（全路径），兼容 PyInstaller 冻结环境。
请求（request_*）：用 PyObjC（已通过 pynput 依赖打包进 .app），避免 ctypes 创建
  CF 对象时与 PyObjC 预加载的 CoreFoundation 冲突导致段错误。

  - 辅助功能（Accessibility）：AXIsProcessTrusted / AXIsProcessTrustedWithOptions
    → ApplicationServices / HIServices 框架
  - 输入监听（Input Monitoring）：CGPreflightListenEventAccess / CGRequestListenEventAccess
    → CoreGraphics 框架

关键设计：
  - check_*()  只检测，不弹窗
  - request_*() 检测 + 若未授权则触发系统标准授权弹窗（已授权则静默返回 True）
"""
import ctypes
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

# 系统框架全路径（ctypes 加载，不依赖 find_library —— 冻结环境中不可用）
_APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


class PermissionStatus(NamedTuple):
    accessibility: bool
    input_monitoring: bool

    @property
    def all_granted(self) -> bool:
        return self.accessibility and self.input_monitoring


# ── 检测（ctypes，仅调返回 bool 的函数，不创建 CF 对象）────────

def check_accessibility() -> bool:
    """检测辅助功能权限（AXIsProcessTrusted）—— 只检测，不弹窗"""
    try:
        lib = ctypes.cdll.LoadLibrary(_APP_SERVICES)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception as e:
        logger.warning("check_accessibility failed: %s", e)
        return True  # 不阻塞启动


def check_input_monitoring() -> bool:
    """检测输入监听权限（CGPreflightListenEventAccess）—— 只检测，不弹窗"""
    try:
        lib = ctypes.cdll.LoadLibrary(_CORE_GRAPHICS)
        lib.CGPreflightListenEventAccess.restype = ctypes.c_bool
        return bool(lib.CGPreflightListenEventAccess())
    except Exception as e:
        logger.warning("check_input_monitoring failed: %s", e)
        return True  # 不阻塞启动


def check_all() -> PermissionStatus:
    """检测全部权限状态"""
    return PermissionStatus(
        accessibility=check_accessibility(),
        input_monitoring=check_input_monitoring(),
    )


# ── 请求（PyObjC，创建 CF 对象需走 PyObjC 桥接避免段错误）──────

def request_accessibility() -> bool:
    """请求辅助功能权限 —— 触发 macOS 系统标准授权弹窗

    已授权 → 静默返回 True
    未授权 → 弹出系统授权对话框（用户需去系统设置打开开关）
    用 PyObjC 的 HIServices/CoreFoundation，避免 ctypes 创建 CF 对象的段错误。
    """
    try:
        from CoreFoundation import CFDictionaryCreate, kCFBooleanTrue
        from HIServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        options = CFDictionaryCreate(
            None,
            [kAXTrustedCheckOptionPrompt],
            [kCFBooleanTrue],
            1,
            None,
            None,
        )
        result = bool(AXIsProcessTrustedWithOptions(options))
        logger.info("request_accessibility: %s", result)
        return result
    except Exception as e:
        logger.warning("request_accessibility (PyObjC) failed: %s", e)
        return check_accessibility()


def request_input_monitoring() -> bool:
    """请求输入监听权限 —— 触发 macOS 系统标准授权弹窗

    已授权 → 静默返回 True
    未授权 → 弹出系统授权对话框
    """
    try:
        from Quartz import CGRequestListenEventAccess
        result = bool(CGRequestListenEventAccess())
        logger.info("request_input_monitoring: %s", result)
        return result
    except Exception as e:
        logger.warning("request_input_monitoring (PyObjC) failed: %s", e)
        return check_input_monitoring()
