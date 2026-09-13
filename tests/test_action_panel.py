"""Quick-action bar keyboard and signal behavior."""

from PyQt5.QtCore import Qt

from ai_desktop.services.action_service import BUILTIN_ACTIONS
from ai_desktop.ui.action_panel import ActionPanel


def test_number_key_executes_action_with_unchanged_material(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("  raw user material  ")
    with qtbot.waitSignal(panel.action_selected, timeout=1000) as signal:
        qtbot.keyClick(panel, Qt.Key_2)
    assert signal.args == ["explain", "  raw user material  "]
    assert not panel.isVisible()


def test_arrow_and_enter_select_action(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material")
    qtbot.keyClick(panel, Qt.Key_Right)
    qtbot.keyClick(panel, Qt.Key_Right)
    with qtbot.waitSignal(panel.action_selected, timeout=1000) as signal:
        qtbot.keyClick(panel, Qt.Key_Return)
    assert signal.args == ["summarize", "material"]


def test_escape_returns_to_free_input_without_action(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material")
    with qtbot.waitSignal(panel.cancelled, timeout=1000):
        with qtbot.assertNotEmitted(panel.action_selected):
            qtbot.keyClick(panel, Qt.Key_Escape)
    assert not panel.isVisible()


def test_hidden_or_extra_actions_are_not_rendered(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS[:2]))
    qtbot.addWidget(panel)
    assert len(panel._buttons) == 2
    panel.refresh_actions(list(BUILTIN_ACTIONS))
    assert len(panel._buttons) == 4
