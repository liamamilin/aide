"""Window placement serialization and multi-screen recovery helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass

from PyQt5.QtCore import QRect, QSize


@dataclass(frozen=True)
class WindowState:
    x: int
    y: int
    width: int | None = None
    height: int | None = None
    screen: str = ""


@dataclass(frozen=True)
class ScreenArea:
    name: str
    geometry: QRect


def _plain_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_window_state(raw: str, *, include_size: bool) -> WindowState | None:
    """Parse a versioned placement record, rejecting partial or unreasonable data."""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None

    x = _plain_int(payload.get("x"))
    y = _plain_int(payload.get("y"))
    if x is None or y is None or abs(x) > 1_000_000 or abs(y) > 1_000_000:
        return None

    width = height = None
    if include_size:
        width = _plain_int(payload.get("width"))
        height = _plain_int(payload.get("height"))
        if width is None or height is None or not (1 <= width <= 100_000 and 1 <= height <= 100_000):
            return None

    screen = payload.get("screen", "")
    if not isinstance(screen, str) or len(screen) > 256:
        return None
    return WindowState(x, y, width, height, screen)


def serialize_window_state(rect: QRect, screen: str, *, include_size: bool) -> str:
    payload = {
        "version": 1,
        "x": rect.x(),
        "y": rect.y(),
        "screen": screen,
    }
    if include_size:
        payload.update({"width": rect.width(), "height": rect.height()})
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _point_distance_squared(x: int, y: int, rect: QRect) -> int:
    nearest_x = min(max(x, rect.left()), rect.right())
    nearest_y = min(max(y, rect.top()), rect.bottom())
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2


def choose_screen(state: WindowState, screens: list[ScreenArea]) -> ScreenArea | None:
    if not screens:
        return None
    if state.screen:
        match = next((screen for screen in screens if screen.name == state.screen), None)
        if match is not None:
            return match

    center_x = state.x + (state.width or 1) // 2
    center_y = state.y + (state.height or 1) // 2
    containing = next(
        (screen for screen in screens if screen.geometry.contains(center_x, center_y)),
        None,
    )
    if containing is not None:
        return containing
    return min(
        screens,
        key=lambda screen: _point_distance_squared(center_x, center_y, screen.geometry),
    )


def fit_window_state(
    state: WindowState,
    screens: list[ScreenArea],
    *,
    fallback_size: QSize,
    minimum_size: QSize,
) -> tuple[QRect, ScreenArea] | None:
    """Fit a saved window completely inside the best current available screen."""
    screen = choose_screen(state, screens)
    if screen is None or screen.geometry.width() <= 0 or screen.geometry.height() <= 0:
        return None

    area = screen.geometry
    requested_width = state.width if state.width is not None else fallback_size.width()
    requested_height = state.height if state.height is not None else fallback_size.height()
    width = min(max(requested_width, min(minimum_size.width(), area.width())), area.width())
    height = min(max(requested_height, min(minimum_size.height(), area.height())), area.height())
    max_x = area.x() + area.width() - width
    max_y = area.y() + area.height() - height
    x = min(max(state.x, area.x()), max_x)
    y = min(max(state.y, area.y()), max_y)
    return QRect(x, y, width, height), screen
