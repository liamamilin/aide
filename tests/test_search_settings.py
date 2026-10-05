from unittest.mock import Mock

import pytest

from ai_desktop import config
from ai_desktop.services.search_credentials import CredentialError
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.ui.settings_dialog import SettingsDialog
from ai_desktop.utils.storage import get_setting, save_setting


def test_settings_keys_never_cross_public_settings_signal(qtbot):
    credentials = Mock()
    dialog = SettingsDialog({}, credentials=credentials)
    qtbot.addWidget(dialog)
    dialog._key_widgets["exa"].setText("secret-exa-example")
    dialog._key_widgets["parallel"].setText("secret-parallel-example")
    with qtbot.waitSignal(dialog.settings_applied) as signal:
        dialog._on_save()
    data = signal.args[0]
    assert "secret" not in repr(data)
    assert data["search_provider"] == "parallel"
    credentials.set.assert_any_call("exa", "secret-exa-example")
    credentials.set.assert_any_call("parallel", "secret-parallel-example")
    credentials.get.assert_not_called()


def test_cancel_does_not_modify_keychain(qtbot):
    credentials = Mock()
    dialog = SettingsDialog({}, credentials=credentials)
    qtbot.addWidget(dialog)
    dialog._key_widgets["exa"].setText("new-key")
    dialog._key_delete["parallel"].setChecked(True)
    dialog.reject()
    credentials.set.assert_not_called()
    credentials.delete.assert_not_called()
    assert not dialog._key_widgets["exa"].text()


def test_empty_preserves_credentials_delete_is_explicit(qtbot):
    credentials = Mock()
    dialog = SettingsDialog({}, credentials=credentials)
    qtbot.addWidget(dialog)
    dialog._key_delete["exa"].setChecked(True)
    dialog._on_save()
    credentials.set.assert_not_called()
    credentials.delete.assert_called_once_with("exa")


def test_keychain_failure_keeps_dialog_and_nonsecret_settings_unsaved(qtbot):
    credentials = Mock()
    credentials.set.side_effect = CredentialError("保存失败")
    dialog = SettingsDialog({}, credentials=credentials)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._key_widgets["exa"].setText("new-key")
    with qtbot.assertNotEmitted(dialog.settings_applied):
        dialog._on_save()
    assert dialog.isVisible() and dialog._save_status.text() == "保存失败"


def test_zero_values_survive_load(qtbot):
    dialog = SettingsDialog({"temperature": 0, "top_k": 0, "top_p": 0})
    qtbot.addWidget(dialog)
    assert dialog._collect()["temperature"] == 0
    assert dialog._collect()["top_k"] == 0
    assert dialog._collect()["top_p"] == 0


def test_provider_specific_mode_and_pages(qtbot):
    dialog = SettingsDialog({"search_provider": "exa"})
    qtbot.addWidget(dialog)
    assert dialog._pages.count() == 5
    assert not dialog._widgets["search_parallel_mode"].isEnabled()
    dialog._widgets["search_provider"].setCurrentIndex(0)
    assert dialog._widgets["search_parallel_mode"].isEnabled()


def test_search_settings_persist_and_invalid_saved_provider_is_ignored(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_PROVIDER", "parallel")
    monkeypatch.setattr(config, "SEARCH_TIMEOUT", 30)
    monkeypatch.setattr(config, "SEARCH_MAX_RESULTS", 5)
    manager = SettingsManager()
    manager.apply({"search_provider": "exa", "search_timeout": 20, "search_max_results": 3,
                   "exa_api_key": "do-not-store"})
    assert get_setting("search_provider") == "exa"
    assert get_setting("exa_api_key") == ""
    save_setting("search_provider", "unknown")
    save_setting("search_max_results", "999")
    manager.load()
    assert manager.current()["search_provider"] == "exa"
    assert config.SEARCH_MAX_RESULTS == 3


@pytest.mark.parametrize("key,value", [("search_provider", "unknown"), ("search_timeout", 121),
                                       ("search_max_results", 100), ("search_parallel_mode", "agentic"),
                                       ("search_max_results", True), ("search_max_results", 3.5)])
def test_search_invalid_config_does_not_mutate(tmp_db, key, value):
    manager = SettingsManager()
    before = manager.current()
    assert not manager.apply({key: value})
    assert manager.current() == before


def test_expanded_task_and_search_limits_save_reload_and_ui(qtbot, tmp_db, monkeypatch):
    values = {'search_max_results': ('SEARCH_MAX_RESULTS', 99),
              'task_max_tool_calls': ('TASK_MAX_TOOL_CALLS', 41),
              'task_max_search_calls': ('TASK_MAX_SEARCH_CALLS', 23),
              'task_max_model_rounds': ('TASK_MAX_MODEL_ROUNDS', 32)}
    for attr, _ in values.values():
        monkeypatch.setattr(config, attr, getattr(config, attr))
    manager = SettingsManager()
    dialog = SettingsDialog(manager.current(), credentials=Mock())
    qtbot.addWidget(dialog)
    for key, (_, value) in values.items():
        assert dialog._widgets[key].maximum() == 99
        dialog._widgets[key].setValue(value)
    with qtbot.waitSignal(dialog.settings_applied) as signal:
        dialog._on_save()
    manager.apply(signal.args[0])
    for key, (attr, value) in values.items():
        assert get_setting(key) == str(value)
        setattr(config, attr, 1)
    manager.load()
    restored = SettingsDialog(manager.current(), credentials=Mock())
    qtbot.addWidget(restored)
    for key, (attr, value) in values.items():
        assert getattr(config, attr) == value
        assert restored._widgets[key].value() == value


@pytest.mark.parametrize('key', ['task_max_tool_calls', 'task_max_search_calls', 'task_max_model_rounds'])
@pytest.mark.parametrize('value', [0, 100, True, 1.5])
def test_invalid_task_limits_are_ignored_on_apply_and_load(tmp_db, key, value):
    manager = SettingsManager()
    before = manager.current()
    assert not manager.apply({key: value})
    save_setting(key, str(value))
    manager.load()
    assert manager.current() == before
