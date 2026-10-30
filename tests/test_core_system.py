"""Model management, export, diagnostics and update checks."""
import json

import pytest

from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.system import SystemService, version_tuple
from ai_desktop.utils import storage
from tests.fake_ollama import FakeOllama


@pytest.fixture
def system(tmp_db):
    events = []
    return SystemService(lambda name, data=None: events.append((name, data))), events


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


def test_models_detail_reports_capabilities(system, fake):
    service, _ = system
    fake.static_responses["/api/tags"] = b'{"models":[{"name":"vis:7b","size":123}]}'
    fake.static_responses["/api/show"] = b'{"capabilities":["completion","vision","tools"]}'
    got = service.models_detail({"base_url": fake.url})
    assert got["reachable"] is True
    assert got["models"] == [{"name": "vis:7b", "size": 123, "vision": True, "tools": True, "thinking": False}]


def test_models_detail_unreachable(system):
    service, _ = system
    assert service.models_detail({"base_url": "http://127.0.0.1:1"}) == {"reachable": False, "models": []}


def test_pull_streams_progress(system, fake):
    service, events = system
    fake.enqueue({"status": "pulling", "total": 10, "completed": 5}, {"status": "success"})
    assert service.models_pull({"model": "m:1b", "base_url": fake.url}) == {"model": "m:1b"}
    assert [e[1]["status"] for e in events] == ["pulling", "success"]
    assert events[0][1]["completed"] == 5


def test_pull_rejects_bad_name_and_server_error(system, fake):
    service, _ = system
    with pytest.raises(ProtocolError):
        service.models_pull({"model": "has space"})
    fake.enqueue({"error": "not found"})
    with pytest.raises(ProtocolError):
        service.models_pull({"model": "nope", "base_url": fake.url})


def _seed():
    convo = storage.create_conversation("assistant", "Trip")
    storage.save_message(convo.id, "user", "Where to?")
    storage.save_message(convo.id, "assistant", "Kyoto.")
    return convo


@pytest.mark.parametrize("fmt", ["markdown", "json"])
def test_export_writes_file(system, tmp_path, fmt):
    service, _ = system
    _seed()
    out = tmp_path / "x" / f"out.{fmt}"
    got = service.export({"format": fmt, "path": str(out)})
    assert got["conversations"] == 1 and got["messages"] == 2
    text = out.read_text(encoding="utf-8")
    assert "Kyoto." in text and "Where to?" in text
    if fmt == "json":
        assert json.loads(text)["conversations"][0]["messages"][0]["role"] == "user"


def test_export_validates(system, tmp_path):
    service, _ = system
    with pytest.raises(ProtocolError):
        service.export({"format": "pdf", "path": str(tmp_path / "a")})
    with pytest.raises(ProtocolError):
        service.export({"format": "json"})


def test_diagnostics_has_no_conversation_text(system, fake):
    service, _ = system
    _seed()
    fake.static_responses["/api/version"] = b'{"version":"0.9.9"}'
    text = service.diagnostics({"base_url": fake.url})["text"]
    assert "Ollama 0.9.9" in text and "Kyoto" not in text and "Where to" not in text


def test_version_compare():
    assert version_tuple("v1.10.0") > version_tuple("1.9.9")
    assert version_tuple("1.5.0-rc1") == (1, 5, 0)


def test_check_update(system, fake, monkeypatch):
    service, _ = system
    monkeypatch.setenv("AIDE_UPDATE_URL", fake.url + "/latest")
    fake.static_responses["/latest"] = json.dumps({
        "tag_name": "v99.0.0", "html_url": "https://example/r",
        "assets": [{"name": "Aide-99.0.0-arm64.dmg", "browser_download_url": "https://example/a.dmg"}]}).encode()
    got = service.check_update({})
    assert got["update"] is True and got["latest"] == "99.0.0" and got["download"].endswith(".dmg")
    fake.static_responses["/latest"] = b'{"tag_name":"v0.0.1"}'
    assert service.check_update({})["update"] is False
