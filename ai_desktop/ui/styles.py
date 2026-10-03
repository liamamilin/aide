"""Theme refresh entry point retained for the controller.

Widget visuals are entirely owned by qfluentwidgets; no application QSS is
applied to buttons, inputs, cards, menus or window controls.
"""
from ai_desktop.ui.fluent import initialize


def refresh_all(app=None) -> int:
    initialize()
    return 0


def invalidate() -> None:
    initialize()
