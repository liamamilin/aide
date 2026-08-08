"""macOS inspired semantic theme tokens used by every desktop surface."""
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
    surface_elevated: str
    surface_muted: str
    separator: str
    focus_ring: str
    disabled: str
    disabled_text: str
    user_bubble: str
    shadow: str


@dataclass(frozen=True)
class LayoutTokens:
    """Shared geometry tokens. Values are device-independent Qt pixels."""

    font_small: int = 11
    font_body: int = 13
    font_title: int = 14
    spacing_xs: int = 4
    spacing_sm: int = 8
    spacing_md: int = 12
    spacing_lg: int = 16
    radius_sm: int = 6
    radius_md: int = 9
    radius_lg: int = 12
    control_height: int = 30
    title_height: int = 44


TOKENS = LayoutTokens()
SYSTEM_FONT = ".AppleSystemUIFont, -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif"


LIGHT = ColorSet(
    window="#f5f5f7",
    surface="#f0f0f2",
    button="#ffffff",
    button_hover="#e9e9ed",
    border="#d2d2d7",
    text="#1d1d1f",
    text_secondary="#6e6e73",
    accent="#0a73e8",
    accent_hover="#0864cc",
    success="#34c759",
    error="#ff3b30",
    surface_elevated="#ffffff",
    surface_muted="#e9e9ec",
    separator="#dedee3",
    focus_ring="#73aef2",
    disabled="#e5e5e9",
    disabled_text="#a1a1a6",
    user_bubble="#0a73e8",
    shadow="#40000000",
)

DARK = ColorSet(
    window="#1c1c1e",
    surface="#242426",
    button="#303033",
    button_hover="#3a3a3d",
    border="#48484c",
    text="#f5f5f7",
    text_secondary="#a1a1a6",
    accent="#0a84ff",
    accent_hover="#0066d6",
    success="#30d158",
    error="#ff453a",
    surface_elevated="#2c2c2e",
    surface_muted="#252527",
    separator="#38383b",
    focus_ring="#409cff",
    disabled="#2a2a2c",
    disabled_text="#68686d",
    user_bubble="#0a84ff",
    shadow="#80000000",
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
    heading=LIGHT.accent,
    bullet=LIGHT.text_secondary,
    hr=LIGHT.border,
    inline_code_bg=LIGHT.surface,
    inline_code_text=LIGHT.text,
    pre_bg="#1e1e1e",
    pre_text="#d4d4d4",
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
    return _MARKDOWN_DARK if is_dark_mode() else _MARKDOWN_LIGHT
