"""Tests for SettingsManager — uses temp SQLite database"""
import tempfile
import threading
from pathlib import Path

import ai_desktop.config as config
import ai_desktop.utils.storage as storage
from ai_desktop.settings_manager import SettingsManager

_ORIG_PATH = storage.DB_PATH

# Save original config defaults
_ORIG_DEFAULTS = {
    "OLLAMA_BASE_URL": config.OLLAMA_BASE_URL,
    "OLLAMA_TIMEOUT": config.OLLAMA_TIMEOUT,
    "OLLAMA_NUM_CTX": config.OLLAMA_NUM_CTX,
    "OLLAMA_NUM_PREDICT": config.OLLAMA_NUM_PREDICT,
    "OLLAMA_TEMPERATURE": config.OLLAMA_TEMPERATURE,
    "OLLAMA_TOP_P": config.OLLAMA_TOP_P,
    "OLLAMA_TOP_K": config.OLLAMA_TOP_K,
    "OLLAMA_REPEAT_PENALTY": config.OLLAMA_REPEAT_PENALTY,
    "OLLAMA_MAX_ROUNDS": config.OLLAMA_MAX_ROUNDS,
    "HOTKEY": config.HOTKEY,
    "TTS_VOICE": config.TTS_VOICE,
}


def _use_temp_db() -> str:
    storage._local = threading.local()
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    storage.DB_PATH = Path(tmp.name)
    tmp.close()
    storage.init_db()
    return tmp.name


def _restore_db() -> None:
    storage.DB_PATH = _ORIG_PATH
    storage._local = threading.local()


def _reset_config() -> None:
    """Reset config module to original defaults"""
    for attr, val in _ORIG_DEFAULTS.items():
        setattr(config, attr, val)


class TestSettingsManagerLoad:
    """Test loading persisted config into config module"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        Path(cls._db_path).unlink(missing_ok=True)
        _restore_db()
        _reset_config()

    def test_load_applies_saved_settings(self):
        _reset_config()
        storage.save_setting("ollama_base_url", "http://custom:1234")
        storage.save_setting("ollama_timeout", "60")
        mgr = SettingsManager()
        mgr.load()
        assert config.OLLAMA_BASE_URL == "http://custom:1234"
        assert config.OLLAMA_TIMEOUT == 60

    def test_load_skips_invalid_values(self):
        _reset_config()
        storage.save_setting("ollama_timeout", "not_a_number")
        mgr = SettingsManager()
        mgr.load()
        # Should keep default (120), not crash
        assert config.OLLAMA_TIMEOUT == 120

    def test_load_skips_empty_values(self):
        _reset_config()
        # Clear all settings for this test
        db = storage._conn()
        db.execute("DELETE FROM settings")
        db.commit()
        mgr = SettingsManager()
        mgr.load()
        assert config.OLLAMA_BASE_URL == "http://localhost:11434"


class TestSettingsManagerApply:
    """Test applying settings changes"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        Path(cls._db_path).unlink(missing_ok=True)
        _restore_db()
        _reset_config()

    def test_apply_updates_config_and_db(self):
        _reset_config()
        mgr = SettingsManager()
        changed = mgr.apply({
            "base_url": "http://newhost:9999",
            "timeout": 30,
            "num_ctx": 4096,
        })
        assert "base_url" in changed
        assert config.OLLAMA_BASE_URL == "http://newhost:9999"
        assert config.OLLAMA_TIMEOUT == 30
        assert storage.get_setting("ollama_base_url") == "http://newhost:9999"

    def test_apply_only_returns_changed_keys(self):
        _reset_config()
        mgr = SettingsManager()
        # Set a known value first
        config.OLLAMA_TIMEOUT = 120
        storage.save_setting("ollama_timeout", "120")
        changed = mgr.apply({"timeout": 120})
        assert "timeout" not in changed

    def test_apply_handles_hotkey_change(self):
        _reset_config()
        mgr = SettingsManager()
        changed = mgr.apply({"hotkey": "<cmd>+<shift>+k"})
        assert "hotkey" in changed
        assert config.HOTKEY == "<cmd>+<shift>+k"
        assert storage.get_setting("hotkey") == "<cmd>+<shift>+k"

    def test_apply_handles_tts_voice_change(self):
        _reset_config()
        mgr = SettingsManager()
        changed = mgr.apply({"tts_voice": "Serena"})
        assert "tts_voice" in changed
        assert config.TTS_VOICE == "Serena"
        assert storage.get_setting("tts_voice") == "Serena"

    def test_apply_rejects_invalid_hotkey(self):
        _reset_config()
        mgr = SettingsManager()
        changed = mgr.apply({"hotkey": "not-a-hotkey"})
        assert "hotkey" not in changed
        assert config.HOTKEY == _ORIG_DEFAULTS["HOTKEY"]
        assert storage.get_setting("hotkey") != "not-a-hotkey"

    def test_apply_skips_unknown_keys(self):
        _reset_config()
        mgr = SettingsManager()
        changed = mgr.apply({"unknown_key": "value"})
        assert "unknown_key" not in changed
