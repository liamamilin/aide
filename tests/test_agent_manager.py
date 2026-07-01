"""Tests for AgentManager — uses temp SQLite database"""
import tempfile
import threading
from pathlib import Path

import ai_desktop.utils.storage as storage
from ai_desktop.agent_manager import AgentManager
from ai_desktop.config import AGENTS, DEFAULT_AGENT_INDEX, Agent

_ORIG_PATH = storage.DB_PATH


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


class TestAgentManagerInit:
    """Test AgentManager initialization"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        Path(cls._db_path).unlink(missing_ok=True)
        _restore_db()

    def test_init_loads_builtin_agents(self):
        mgr = AgentManager()
        assert len(mgr.all_agents) >= len(AGENTS)
        assert mgr.active_agent.id == AGENTS[DEFAULT_AGENT_INDEX].id

    def test_init_restores_saved_agent(self):
        storage.save_setting("last_agent_id", "translator")
        mgr = AgentManager()
        assert mgr.active_agent.id == "translator"

    def test_init_falls_back_on_invalid_saved_agent(self):
        storage.save_setting("last_agent_id", "nonexistent_agent")
        mgr = AgentManager()
        assert mgr.active_agent.id == AGENTS[DEFAULT_AGENT_INDEX].id

    def test_init_skips_invalid_custom_agent_data(self):
        storage.save_setting("custom_agents",
                             '[{"id":"broken"}, {"id":"custom_ok","name":"OK","icon":"","system_prompt":"prompt"}]')
        mgr = AgentManager()
        ids = [a.id for a in mgr.all_agents]
        assert "broken" not in ids
        assert "custom_ok" in ids


class TestAgentManagerSwitch:
    """Test agent switching"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        Path(cls._db_path).unlink(missing_ok=True)
        _restore_db()

    def test_switch_changes_active_agent(self):
        mgr = AgentManager()
        translator = next(a for a in AGENTS if a.id == "translator")
        result = mgr.switch(translator)
        assert result.id == "translator"
        assert mgr.active_agent.id == "translator"
        assert storage.get_setting("last_agent_id") == "translator"

    def test_switch_falls_back_on_unknown_agent(self):
        mgr = AgentManager()
        fake = Agent(id="nonexistent", name="X", icon="X", system_prompt="X")
        result = mgr.switch(fake)
        assert result.id == AGENTS[DEFAULT_AGENT_INDEX].id


class TestAgentManagerSaveCustom:
    """Test saving custom agents"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        Path(cls._db_path).unlink(missing_ok=True)
        _restore_db()

    def test_save_custom_agents_rebuilds_list(self):
        mgr = AgentManager()
        custom_data = [
            {"id": "custom_1", "name": "设计师", "icon": "🎨", "system_prompt": "你是设计师"}
        ]
        mgr.save_custom(custom_data)
        assert len(mgr.all_agents) == len(AGENTS) + 1
        assert mgr.custom_agents[0].name == "设计师"

    def test_save_custom_preserves_active_if_still_valid(self):
        mgr = AgentManager()
        translator = next(a for a in AGENTS if a.id == "translator")
        mgr.switch(translator)
        custom_data = [
            {"id": "custom_1", "name": "设计师", "icon": "🎨", "system_prompt": "你是设计师"}
        ]
        mgr.save_custom(custom_data)
        # Active agent should still be translator
        assert mgr.active_agent.id == "translator"

    def test_save_custom_resets_active_if_removed(self):
        mgr = AgentManager()
        custom_data = [
            {"id": "custom_1", "name": "设计师", "icon": "🎨", "system_prompt": "你是设计师"}
        ]
        mgr.save_custom(custom_data)
        # Now switch to custom_1
        custom_agent = next(a for a in mgr.all_agents if a.id == "custom_1")
        mgr.switch(custom_agent)
        # Remove custom_1
        mgr.save_custom([])
        # Active should fall back to default
        assert mgr.active_agent.id == AGENTS[DEFAULT_AGENT_INDEX].id

    def test_save_custom_filters_invalid_and_duplicate_agents(self):
        mgr = AgentManager()
        mgr.save_custom([
            {"id": "custom_1", "name": "设计师", "icon": "🎨", "system_prompt": "你是设计师"},
            {"id": "custom_1", "name": "重复", "icon": "🎨", "system_prompt": "重复"},
            {"id": "broken", "name": "", "icon": "", "system_prompt": ""},
            {"id": "translator", "name": "覆盖内置", "icon": "X", "system_prompt": "X"},
        ])
        custom_ids = [a.id for a in mgr.custom_agents]
        assert custom_ids == ["custom_1"]
