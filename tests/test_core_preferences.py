"""Settings and custom-agent methods exposed to the native shell."""
import pytest

from ai_desktop import config
from ai_desktop.core.preferences import PreferencesService
from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.runs import RunService
from ai_desktop.llm.thinking import ThinkMode, ThinkSetting
from tests.fake_ollama import FakeOllama


@pytest.fixture
def prefs(tmp_db, monkeypatch):
    for attr in ("OLLAMA_BASE_URL", "OLLAMA_TEMPERATURE", "OLLAMA_NUM_CTX", "OLLAMA_THINK"):
        monkeypatch.setattr(config, attr, getattr(config, attr))
    return PreferencesService(RunService(lambda *_: None))


def test_get_exposes_only_everyday_and_advanced_keys(prefs):
    got = prefs.get({})
    assert set(got["settings"]) == {"base_url", "think", "timeout", "num_ctx", "num_predict", "temperature",
                                    "top_p", "top_k", "repeat_penalty", "max_rounds"}
    assert got["settings"]["think"]["mode"] in {"on", "off", "model_default"}
    assert got["hotkey"] == config.HOTKEY


def test_set_applies_persists_and_reloads(prefs):
    out = prefs.set({"settings": {"temperature": 0.3, "num_ctx": 8192, "think": {"mode": "off"}}})
    assert set(out["changed"]) == {"temperature", "num_ctx", "think"}
    assert config.OLLAMA_TEMPERATURE == 0.3 and config.OLLAMA_THINK == ThinkSetting(ThinkMode.OFF)
    config.OLLAMA_TEMPERATURE = 0.9
    prefs._settings.load()
    assert config.OLLAMA_TEMPERATURE == 0.3 and prefs.get({})["settings"]["think"] == {"mode": "off"}


@pytest.mark.parametrize("bad", [
    {"temperature": 5}, {"num_ctx": 100.5}, {"top_p": "x"}, {"base_url": "ftp://x"}, {"think": {"mode": "named"}},
    {"hotkey": "<cmd>+k"}, {"temperature": True}, {},
])
def test_set_rejects_invalid(prefs, bad):
    with pytest.raises(ProtocolError):
        prefs.set({"settings": bad})
    assert config.OLLAMA_TEMPERATURE == 0.7 or config.OLLAMA_TEMPERATURE != 5


def test_test_connection_reports_models(prefs):
    fake = FakeOllama()
    try:
        fake.static_responses["/api/tags"] = b'{"models":[{"name":"a"},{"name":"b"}]}'
        assert prefs.test_connection({"base_url": fake.url}) == {"ok": True, "model_count": 2}
        assert prefs.test_connection({"base_url": "http://127.0.0.1:1"}) == {"ok": False, "model_count": 0}
    finally:
        fake.close()
    with pytest.raises(ProtocolError):
        prefs.test_connection({"base_url": "nope"})


def test_custom_agent_lifecycle(prefs):
    created = prefs.save_agent({"name": " 周报 ", "system_prompt": "写周报", "icon": ""})["id"]
    listed = {a.id: a for a in prefs._runs.agents}
    assert listed[created].name == "周报" and listed[created].icon == "🤖"
    prefs.save_agent({"id": created, "name": "日报", "system_prompt": "写日报", "icon": "📝"})
    assert {a.id: a for a in prefs._runs.agents}[created].name == "日报"
    with pytest.raises(ProtocolError):
        prefs.save_agent({"id": "general_assistant", "name": "x", "system_prompt": "x"})
    with pytest.raises(ProtocolError):
        prefs.save_agent({"name": "", "system_prompt": "x"})
    with pytest.raises(ProtocolError):
        prefs.delete_agent({"id": "general_assistant"})
    prefs.delete_agent({"id": created})
    assert created not in {a.id for a in prefs._runs.agents}
