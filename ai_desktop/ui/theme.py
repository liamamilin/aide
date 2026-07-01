"""
主题工具 —— 系统暗色检测 + 全量语义颜色集

提供两套显式颜色（亮/暗），由 styles.py 在首次渲染时懒加载。
与 palette() 不同，显式颜色保证两种模式下均有足够对比度。
"""
from dataclasses import dataclass

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication


@dataclass(frozen=True)
class ColorSet:
    window: str          # 应用背景
    surface: str         # 标题栏 / 工具栏 / 输入栏背景
    button: str          # 按钮 / 气泡背景
    button_hover: str    # 按钮 hover
    border: str          # 边框（两种模式均可见）
    text: str            # 主文字
    text_secondary: str  # 次要文字 / 图标
    accent: str          # 强调色（蓝色）
    accent_hover: str    # 强调色 hover
    success: str         # 成功/连接
    error: str           # 错误/断开


LIGHT = ColorSet(
    window="#f5f5f7",
    surface="#e8e8ed",
    button="#d1d1d6",
    button_hover="#c1c1c6",
    border="#b0b0b5",
    text="#1a1a1a",
    text_secondary="#666666",
    accent="#007AFF",
    accent_hover="#0066d6",
    success="#34c759",
    error="#ff3b30",
)

DARK = ColorSet(
    window="#1a1a1a",
    surface="#2d2d2d",
    button="#3a3a3c",
    button_hover="#4a4a4c",
    border="#5a5a5c",
    text="#e0e0e0",
    text_secondary="#999999",
    accent="#0a84ff",
    accent_hover="#0066d6",
    success="#30d158",
    error="#ff453a",
)


def is_dark_mode() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.Window).lightness() < 128


def current() -> ColorSet:
    return DARK if is_dark_mode() else LIGHT


# ── Markdown 颜色（与 ColorSet 一致）───────────────────

@dataclass(frozen=True)
class MarkdownColors:
    heading: str
    bullet: str
    hr: str
    inline_code_bg: str
    inline_code_text: str
    pre_bg: str
    pre_text: str


_MARKDOWN_LIGHT = MarkdownColors(
    heading=LIGHT.text,
    bullet=LIGHT.text_secondary,
    hr=LIGHT.border,
    inline_code_bg=LIGHT.surface,
    inline_code_text=LIGHT.text,
    pre_bg="#1e1e1e",
    pre_text="#d4d4d4",
)

_MARKDOWN_DARK = MarkdownColors(
    heading=DARK.text,
    bullet=DARK.text_secondary,
    hr=DARK.border,
    inline_code_bg=DARK.surface,
    inline_code_text=DARK.text,
    pre_bg="#0d0d0d",
    pre_text="#d4d4d4",
)


def current_markdown() -> MarkdownColors:
    return _MARKDOWN_DARK if is_dark_mode() else _MARKDOWN_LIGHT
