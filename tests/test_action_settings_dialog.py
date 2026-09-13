"""Quick-action settings UI tests."""

from unittest.mock import patch

from PyQt5.QtWidgets import QMessageBox

from ai_desktop.config import Agent
from ai_desktop.services.action_service import BUILTIN_ACTIONS
from ai_desktop.services.model_profiles import ModelProfile
from ai_desktop.ui.action_settings_dialog import ActionSettingsDialog

AGENTS = [
    Agent("translator", "翻译", "🌐", "Translate."),
    Agent("code_expert", "代码专家", "💻", "Explain."),
    Agent("summarizer", "摘要", "📄", "Summarize."),
    Agent("polisher", "润色", "✍️", "Rewrite."),
]


def test_dialog_emits_renamed_hidden_and_rebound_actions(qtbot):
    profile = ModelProfile("fast", "快速")
    dialog = ActionSettingsDialog(list(BUILTIN_ACTIONS), AGENTS, [profile])
    qtbot.addWidget(dialog)
    row = dialog._rows["translate"]
    row["name"].setText("中英互译")
    row["agent"].setCurrentIndex(row["agent"].findData("code_expert"))
    row["profile"].setCurrentIndex(row["profile"].findData("fast"))
    row["pinned"].setCurrentIndex(row["pinned"].findData(None))
    row["enabled"].setChecked(False)

    with qtbot.waitSignal(dialog.actions_saved, timeout=1000) as signal:
        dialog._on_save()

    action = next(item for item in signal.args[0] if item.id == "translate")
    assert action.name == "中英互译"
    assert action.agent_id == "code_expert"
    assert action.profile_id == "fast"
    assert action.pinned_order is None
    assert action.enabled is False


def test_duplicate_shortcut_order_is_rejected(qtbot):
    dialog = ActionSettingsDialog(list(BUILTIN_ACTIONS), AGENTS, [])
    qtbot.addWidget(dialog)
    explain = dialog._rows["explain"]["pinned"]
    explain.setCurrentIndex(explain.findData(0))
    with patch.object(QMessageBox, "warning") as warning:
        with qtbot.assertNotEmitted(dialog.actions_saved):
            dialog._on_save()
    warning.assert_called_once()
