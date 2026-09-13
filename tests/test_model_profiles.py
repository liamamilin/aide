"""Task model profile validation, persistence, assignment, and resolution."""

import json
from dataclasses import replace

import pytest

from ai_desktop import config
from ai_desktop.agent_manager import AgentManager
from ai_desktop.services.model_profiles import (
    ModelOverrides,
    ModelProfile,
    ModelProfileManager,
    validate_profile,
)
from ai_desktop.utils import storage


def test_profile_round_trip_and_agent_assignment(tmp_db):
    manager = ModelProfileManager()
    saved = manager.save(
        ModelProfile(
            "translation-short",
            "翻译短答",
            "qwen-text",
            False,
            0.2,
            512,
        )
    )
    assert saved.updated_at > 0
    reloaded = ModelProfileManager()
    assert reloaded.profiles == [saved]

    agents = AgentManager()
    agents.assign_profile("translator", saved.id)
    assert next(agent for agent in agents.all_agents if agent.id == "translator").profile_id == saved.id
    assert storage.load_agent_profile_assignments() == {"translator": saved.id}

    assert reloaded.delete(saved.id)
    assert storage.load_agent_profile_assignments() == {}


@pytest.mark.parametrize(
    "profile, message",
    [
        (ModelProfile("bad id", "有效"), "配置 ID"),
        (ModelProfile("valid", ""), "配置名称"),
        (ModelProfile("valid", "有效", temperature=2.1), "temperature"),
        (ModelProfile("valid", "有效", num_predict=0), "输出上限"),
        (ModelProfile("valid", "有效", think="yes"), "思考选项"),
    ],
)
def test_profile_validation_rejects_invalid_values(profile, message):
    with pytest.raises(ValueError, match=message):
        validate_profile(profile)


def test_resolution_is_fieldwise_and_uses_documented_precedence(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_THINK", True)
    monkeypatch.setattr(config, "OLLAMA_TEMPERATURE", 0.7)
    monkeypatch.setattr(config, "OLLAMA_NUM_PREDICT", 2048)
    manager = ModelProfileManager()
    manager.save(ModelProfile("agent", "代码配置", "agent-model", True, 0.4, 1024))
    manager.save(ModelProfile("action", "摘要配置", None, False, None, 256))

    resolved = manager.resolve(
        global_model="global-model",
        agent_profile_id="agent",
        action_profile_id="action",
        temporary=ModelOverrides(temperature=1.1),
        available_models=["global-model", "agent-model"],
    )
    assert resolved.model == "agent-model"
    assert resolved.think is False
    assert resolved.temperature == 1.1
    assert resolved.num_predict == 256
    assert resolved.options == {"temperature": 1.1, "num_predict": 256}


def test_missing_model_and_deleted_profile_fall_back_with_visible_warnings(tmp_db):
    manager = ModelProfileManager()
    manager.save(ModelProfile("missing", "离线模型", "removed-model", False, 0.2, 64))
    resolved = manager.resolve(
        global_model="available-model",
        agent_profile_id="missing",
        action_profile_id="deleted",
        available_models=["available-model"],
    )
    assert resolved.model == "available-model"
    assert resolved.think is False
    assert resolved.temperature == 0.2
    assert resolved.num_predict == 64
    assert any("已被删除" in warning for warning in resolved.warnings)
    assert any("当前不可用" in warning for warning in resolved.warnings)


def test_known_empty_model_list_falls_back_instead_of_assuming_unknown(tmp_db):
    manager = ModelProfileManager()
    manager.save(ModelProfile("missing", "离线模型", "removed-model"))
    resolved = manager.resolve(
        global_model="global-model",
        agent_profile_id="missing",
        available_models=[],
    )
    assert resolved.model == "global-model"
    assert any("当前不可用" in warning for warning in resolved.warnings)


def test_replacing_profiles_is_atomic_at_validation_boundary(tmp_db):
    manager = ModelProfileManager()
    original = manager.save(ModelProfile("one", "一"))
    with pytest.raises(ValueError, match="不能重复"):
        manager.replace_all([original, replace(original, name="重复")])
    assert ModelProfileManager().profiles == [original]


def test_corrupt_profile_record_is_ignored(tmp_db):
    storage.save_model_profile_record(
        {
            "id": "broken",
            "name": "坏配置",
            "options": json.dumps({"temperature": "hot"}),
            "updated_at": 1,
        }
    )
    assert ModelProfileManager().profiles == []


def test_request_snapshot_accepts_resolved_profile_values(tmp_db, monkeypatch):
    from ai_desktop.llm.chat_client import ChatClient
    from ai_desktop.utils.storage import Message

    monkeypatch.setattr(config, "OLLAMA_THINK", True)
    monkeypatch.setattr(config, "OLLAMA_TEMPERATURE", 0.7)
    request = ChatClient(model="profile-model").create_request(
        [Message("user", "question")],
        think=False,
        options={"temperature": 0.15, "num_predict": 321},
    )
    monkeypatch.setattr(config, "OLLAMA_THINK", True)
    monkeypatch.setattr(config, "OLLAMA_TEMPERATURE", 1.9)
    assert request.model == "profile-model"
    assert request.think is False
    assert dict(request.options)["temperature"] == 0.15
    assert dict(request.options)["num_predict"] == 321


def test_profile_editor_preserves_inheritance_and_explicit_values(qtbot):
    from ai_desktop.ui.model_profile_dialog import _ModelProfileEditDialog

    dialog = _ModelProfileEditDialog(
        "编辑模型配置",
        ModelProfile("focused", "专注", "model-a", False, 0.25, 640),
        ["model-a", "model-b"],
    )
    qtbot.addWidget(dialog)
    assert dialog.profile().model == "model-a"
    assert dialog.profile().think is False
    assert dialog.profile().temperature == 0.25
    assert dialog.profile().num_predict == 640

    dialog._temperature_enabled.setChecked(False)
    dialog._tokens_enabled.setChecked(False)
    assert dialog.profile().temperature is None
    assert dialog.profile().num_predict is None


def test_agent_editor_clears_deleted_profile_and_emits_binding(qtbot):
    from ai_desktop.ui.agent_editor import AgentDef, AgentEditor

    profile = ModelProfile("focused", "专注")
    agent = AgentDef("builtin", "内置", "🤖", "Help.", True, profile.id)
    dialog = AgentEditor([agent], [], profiles=[profile])
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.agent_profile_changed, timeout=1000) as signal:
        dialog._on_profiles_updated([])
    assert signal.args == ["builtin", None]
    assert agent.profile_id is None


def test_deleted_profile_is_not_restored_from_custom_agent_json(tmp_db):
    manager = ModelProfileManager()
    profile = manager.save(ModelProfile("old", "旧配置"))
    storage.save_setting(
        "custom_agents",
        json.dumps([
            {
                "id": "custom_1",
                "name": "自定义",
                "icon": "🤖",
                "system_prompt": "Help.",
                "profile_id": profile.id,
            }
        ], ensure_ascii=False),
    )
    manager.delete(profile.id)

    agent = next(item for item in AgentManager().all_agents if item.id == "custom_1")
    assert agent.profile_id is None
