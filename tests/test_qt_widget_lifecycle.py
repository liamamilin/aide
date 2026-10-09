"""Bound native painting regressions in a child process."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("registration", ["addWidget", "add_widget"])
def test_registered_window_survives_gc_during_svg_paint(registration):
    # Force the collection that used to delete ActionPanel after its test
    # function returned, while pytest-qt was painting the combo-box arrow.
    # A native crash cannot be caught by a Python assertion or qtbot timeout.
    program = textwrap.dedent('''
        import gc
        import pytest

        state = {"paints": 0, "destroyed": 0, "destroyed_during_paint": 0,
                 "painting": False}

        class PaintCollection:
            def pytest_collection_finish(self, session):
                # conftest must set the temporary data root before app imports.
                import qfluentwidgets.common.icon as icons
                from ai_desktop.ui.action_panel import ActionPanel

                original_draw = icons.drawSvgIcon
                original_init = ActionPanel.__init__

                def on_destroyed():
                    state["destroyed"] += 1
                    if state["painting"]:
                        state["destroyed_during_paint"] += 1
                        print("WINDOW_DESTROYED_DURING_PAINT", flush=True)

                def tracked_init(self, *args, **kwargs):
                    original_init(self, *args, **kwargs)
                    self.destroyed.connect(on_destroyed)

                def draw_with_collection(icon, painter, rect):
                    state["paints"] += 1
                    state["painting"] = True
                    try:
                        gc.collect()
                        # Exercise real SVG rendering with the active painter.
                        original_draw(icon, painter, rect)
                    finally:
                        state["painting"] = False

                ActionPanel.__init__ = tracked_init
                icons.drawSvgIcon = draw_with_collection

            def pytest_runtest_call(self, item):
                bot = item.funcargs["qtbot"]
                bot.addWidget = getattr(bot, REGISTRATION)

        assert gc.isenabled()
        result = pytest.main([
            "tests/test_action_panel.py::test_number_key_executes_action_with_unchanged_material",
            "-q", "-s",
        ], plugins=[PaintCollection()])
        assert result == 0, result
        assert state["paints"] > 0, state
        assert state["destroyed"] == 1, state
        assert state["destroyed_during_paint"] == 0, state
        assert gc.isenabled()
    ''').replace("REGISTRATION", repr(registration))
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", program],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
