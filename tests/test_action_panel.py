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
    assert signal.args == ["explain", "  raw user material  ", "new"]
    assert panel.isVisible()


def test_arrow_and_enter_select_action(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material")
    qtbot.keyClick(panel, Qt.Key_Right)
    qtbot.keyClick(panel, Qt.Key_Right)
    with qtbot.waitSignal(panel.action_selected, timeout=1000) as signal:
        qtbot.keyClick(panel, Qt.Key_Return)
    assert signal.args == ["summarize", "material", "new"]


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


def test_existing_conversation_can_continue_in_place(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material", has_conversation=True, mode="current")
    assert panel._mode_combo.isEnabled()
    assert panel._mode_combo.currentData() == "current"
    with qtbot.waitSignal(panel.action_selected, timeout=1000) as signal:
        qtbot.keyClick(panel, Qt.Key_1)
    assert signal.args == ["translate", "material", "current"]


def test_empty_conversation_forces_new_mode(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material", has_conversation=False, mode="current")
    assert not panel._mode_combo.isEnabled()
    assert panel._mode_combo.currentData() == "new"


def test_empty_material_explains_input_requirement_and_disables_actions(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("")
    assert all(not button.isEnabled() for button in panel._buttons)
    assert "输入或粘贴文字" in panel._material_hint.text()
    with qtbot.assertNotEmitted(panel.action_selected):
        qtbot.keyClick(panel, Qt.Key_1)


def test_material_update_enables_actions(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("")
    panel.set_material("translate this")
    assert all(button.isEnabled() for button in panel._buttons)
    assert "已准备 14 个字符" in panel._material_hint.text()


def test_action_panel_stays_open_until_explicitly_collapsed(qtbot):
    panel = ActionPanel(list(BUILTIN_ACTIONS))
    qtbot.addWidget(panel)
    panel.show_for_material("material")
    with qtbot.waitSignal(panel.action_selected, timeout=1000):
        panel._trigger(0)
    assert panel.isVisible()
    with qtbot.waitSignal(panel.cancelled, timeout=1000):
        panel._collapse()
    assert not panel.isVisible()
