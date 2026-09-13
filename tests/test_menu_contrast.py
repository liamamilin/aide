"""菜单对比度测试 —— 确保右键菜单文字/背景满足 WCAG AA（≥4.5:1）。

该测试为纯函数单测，不依赖 Qt 事件循环：通过 monkeypatch 强制亮/暗主题，
重新生成 styles.MENU，校验其中使用的 text/window 颜色对比度达标。
"""
import pytest

from ai_desktop.ui import styles, theme


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = _luminance(_hex_to_rgb(a)), _luminance(_hex_to_rgb(b))
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(
    "dark,expected",
    [(False, theme.LIGHT), (True, theme.DARK)],
)
def test_menu_text_background_contrast(dark, expected, monkeypatch):
    """亮/暗两套主题下，菜单项文字与背景对比度均应 ≥ 4.5:1 (WCAG AA)。"""
    monkeypatch.setattr(theme, "is_dark_mode", lambda: dark)
    styles._generated = None  # 强制按当前主题重新生成样式
    menu = styles.MENU

    ratio = contrast_ratio(expected.text, expected.window)
    assert ratio >= 4.5, f"{'暗' if dark else '亮'}色模式菜单对比度 {ratio:.2f}:1 低于 4.5:1"

    # 样式字符串确实引用了对应颜色（前景文字 + 背景）
    assert expected.text in menu
    assert expected.window in menu


@pytest.mark.parametrize(
    "dark,expected",
    [(False, theme.LIGHT), (True, theme.DARK)],
)
def test_menu_selected_item_contrast(dark, expected, monkeypatch):
    """选中项：强调色背景 + 白色文字 —— 作为 UI 组件/大字号满足 WCAG 3:1。"""
    monkeypatch.setattr(theme, "is_dark_mode", lambda: dark)
    styles._generated = None
    menu = styles.MENU

    ratio = contrast_ratio("#ffffff", expected.accent)
    assert ratio >= 3.0, f"{'暗' if dark else '亮'}色模式选中项对比度 {ratio:.2f}:1 低于 3:1"
    assert "color: white" in menu


@pytest.mark.parametrize(
    "dark,expected",
    [(False, theme.LIGHT), (True, theme.DARK)],
)
def test_menu_style_follows_current_theme(dark, expected, monkeypatch):
    """menu_style() 每次现取主题色（绕过缓存），运行时右键菜单能跟随系统明暗切换。"""
    monkeypatch.setattr(theme, "is_dark_mode", lambda: dark)
    menu = styles.menu_style()

    assert expected.text in menu
    assert expected.window in menu

    # 与缓存版 MENU 同源：清缓存后重新生成的字符串应完全一致
    styles._generated = None
    assert menu == styles.MENU


def test_menu_style_reacts_to_theme_switch(monkeypatch):
    """主题切换后无需清缓存，menu_style() 立即返回新模式颜色。"""
    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    light = styles.menu_style()
    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    dark = styles.menu_style()
    assert light != dark


def test_invalidate_clears_cache(monkeypatch):
    """invalidate() 清空缓存后，MENU 按当前主题重新生成。"""
    monkeypatch.setattr(theme, "is_dark_mode", lambda: True)
    styles._generated = None
    dark_menu = styles.MENU

    monkeypatch.setattr(theme, "is_dark_mode", lambda: False)
    assert styles.MENU is dark_menu  # 缓存仍指向旧串
    styles.invalidate()
    assert styles.MENU is not dark_menu
    assert theme.LIGHT.text in styles.MENU
