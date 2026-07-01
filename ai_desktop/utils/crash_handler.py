import faulthandler
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from ai_desktop.__init__ import __version__

logger = logging.getLogger(__name__)

_CRASH_LOG_DIR = Path.home() / "Library" / "Logs" / "ai-desktop-assistant"


def install() -> None:
    """安装崩溃处理钩子

    - faulthandler: 捕获 C 级 segfault（写入 stderr + 日志文件）
    - sys.excepthook: 捕获 Python 未处理异常（写入日志 + 弹窗）
    """
    _CRASH_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # faulthandler — 捕获 C 级崩溃
    fault_path = _CRASH_LOG_DIR / "fault.log"
    try:
        faulthandler.enable(file=fault_path.open("a"), all_threads=True)
    except Exception:
        pass

    # sys.excepthook — 捕获 Python 异常
    sys.excepthook = _on_crash


def _on_crash(exc_type: type, exc_value: BaseException, exc_tb: Optional[object]) -> None:
    """未处理异常处理函数"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = "".join(tb_lines)
    crash_text = (
        f"=== CRASH at {now} ===\n"
        f"Version: {__version__}\n"
        f"Type: {exc_type.__name__}\n"
        f"Value: {exc_value}\n"
        f"Traceback:\n{tb_text}\n"
    )

    # 写入日志文件
    log_path = _CRASH_LOG_DIR / "crash.log"
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(crash_text + "\n")
    except Exception:
        pass

    logger.error("Unhandled exception: %s", crash_text)

    # 弹窗通知用户（仅在 GUI 模式且有活跃窗口时）
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is not None and app.activeWindow() is not None:
            QMessageBox.critical(
                None,
                "AI 桌面助手 — 意外错误",
                f"程序遇到意外错误，已保存崩溃日志：<br><br>"
                f"<tt>{log_path}</tt><br><br>"
                f"请将此日志内容反馈给开发者。",
            )
    except Exception:
        pass
