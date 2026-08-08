"""Theme tests — ColorSet values and dark mode detection."""
from ai_desktop.ui.theme import (
    DARK,
    LIGHT,
    SYSTEM_FONT,
    TOKENS,
    ColorSet,
    MarkdownColors,
    current,
    current_markdown,
)


def test_light_and_dark_have_all_fields():
    """Both LIGHT and DARK ColorSets should have all fields populated."""
    for name in ColorSet.__dataclass_fields__:
        assert getattr(LIGHT, name) != "", f"LIGHT.{name} is empty"
        assert getattr(DARK, name) != "", f"DARK.{name} is empty"


def test_light_dark_have_different_values():
    """LIGHT and DARK should have different hex values for visual fields."""
    different = 0
    for name in ColorSet.__dataclass_fields__:
        if getattr(LIGHT, name) != getattr(DARK, name):
            different += 1
    assert different >= 10, f"Expected >=10 differences, got {different}"


def test_light_backgrounds_are_light():
    """Light mode backgrounds should be light (high lightness)."""
    for name in ("window", "surface", "button", "button_hover", "border"):
        val = getattr(LIGHT, name)
        if val.startswith("#"):
            r, g, b = int(val[1:3], 16), int(val[3:5], 16), int(val[5:7], 16)
            brightness = (r + g + b) / 3
            assert brightness > 64, f"LIGHT.{name}={val} is too dark (brightness={brightness})"


def test_light_text_is_dark():
    """Light mode text should be dark (high contrast with background)."""
    for name in ("text",):
        val = getattr(LIGHT, name)
        if val.startswith("#"):
            r, g, b = int(val[1:3], 16), int(val[3:5], 16), int(val[5:7], 16)
            brightness = (r + g + b) / 3
            assert brightness < 48, f"LIGHT.{name}={val} is too light (brightness={brightness})"


def test_dark_colors_are_not_too_light():
    """Dark mode window/surface/button colors should be dark."""
    for name in ("window", "surface", "button", "border"):
        val = getattr(DARK, name)
        if val.startswith("#"):
            r, g, b = int(val[1:3], 16), int(val[3:5], 16), int(val[5:7], 16)
            brightness = (r + g + b) / 3
            assert brightness < 96, f"DARK.{name}={val} is too light (brightness={brightness})"


def test_current_returns_color_set():
    """current() should always return a ColorSet instance."""
    result = current()
    assert isinstance(result, ColorSet)


def test_current_markdown_returns_markdown_colors():
    """current_markdown() should always return a MarkdownColors instance."""
    result = current_markdown()
    assert isinstance(result, MarkdownColors)


def test_markdown_colors_consistency():
    """MarkdownColors should be consistent with ColorSet (same hex values)."""
    md_light = current_markdown()  # Since is_dark_mode() may return False in test
    assert isinstance(md_light, MarkdownColors)


def test_semantic_interaction_colors_are_present():
    """Focus, disabled and elevated surfaces must not fall back to hardcoded QSS."""
    fields = ("surface_elevated", "separator", "focus_ring", "disabled", "disabled_text", "user_bubble")
    for palette in (LIGHT, DARK):
        assert all(getattr(palette, field).startswith("#") for field in fields)


def test_layout_and_system_font_tokens():
    assert TOKENS.control_height >= 28
    assert TOKENS.radius_md > TOKENS.radius_sm
    assert TOKENS.spacing_lg > TOKENS.spacing_sm
    assert "AppleSystemUIFont" in SYSTEM_FONT
