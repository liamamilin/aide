"""
Fluent Light 下的消息内容语义颜色

组件样式由 qfluentwidgets 管理；此处仅供 Markdown 内容与宠物状态使用。
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
    window="#f0f4f9",
    surface="#ffffff",
    button="#ffffff",
    button_hover="#f9f9f9",
    border="#e5e5e5",
    text="#000000",
    text_secondary="#666666",
    accent="#009faa",
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
    return LIGHT


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
    pre_bg=LIGHT.window,
    pre_text=LIGHT.text,
)

_MARKDOWN_DARK = MarkdownColors(
    heading=DARK.accent,
    bullet=DARK.text_secondary,
    hr=DARK.border,
    inline_code_bg=DARK.surface,
    inline_code_text=DARK.text,
    pre_bg="#0d0d0d",
    pre_text="#d4d4d4",
)


def current_markdown() -> MarkdownColors:
    return _MARKDOWN_LIGHT
