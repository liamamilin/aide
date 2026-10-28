"""Quick-action runs over the Qt-free transport, against the loopback fake Ollama."""
import json
import queue

import pytest

from ai_desktop import config
from ai_desktop.core.runs import RunService
from ai_desktop.llm.sync_stream import SyncChatTransport  # noqa: F401  (import must not need Qt)
from ai_desktop.llm.thinking import ThinkingCapability
from tests.fake_ollama import FakeOllama


def _frame(content="", done=False, **extra):
    return {"message": {"role": "assistant", "content": content}, "done": done, **extra}


@pytest.fixture
def service(tmp_db, monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", fake.url)
    monkeypatch.setattr("ai_desktop.core.runs.thinking_capability", lambda model: ThinkingCapability())
    events: queue.Queue = queue.Queue()
    svc = RunService(lambda name, data: events.put((name, data)))
    yield svc, fake, events
    fake.close()


def _until_finished(events, timeout=10):
    deltas = []
    while True:
        name, data = events.get(timeout=timeout)
        if name == "run.finished":
            return deltas, data
        deltas.append(data)


def test_translate_streams_deltas_then_finishes(service):
    svc, fake, events = service
    fake.enqueue(_frame("Hel"), _frame("lo"), _frame("", done=True, done_reason="stop"))
    started = svc.start({"action_id": "translate", "material": "你好"})
    deltas, finished = _until_finished(events)
    assert [d["text"] for d in deltas if d["kind"] == "content"] == ["Hel", "lo"]
    assert all(d["run_id"] == started["run_id"] for d in deltas)
    assert finished["status"] == "succeeded" and finished["text"] == "Hello"
    sent = fake.requests[0]["payload"]
    assert sent["messages"][-1] == {"role": "user", "content": "你好"}
    assert "tools" not in sent


def test_cancel_stops_a_held_stream(service):
    svc, fake, events = service
    scenario = fake.enqueue(_frame("partial"), hold_open=True)
    started = svc.start({"action_id": "explain", "material": "x"})
    name, data = events.get(timeout=10)
    assert (name, data["text"]) == ("run.delta", "partial")
    assert svc.cancel({"run_id": started["run_id"]}) == {"cancelled": True}
    _, finished = _until_finished(events)
    assert finished["status"] == "cancelled"
    assert scenario.disconnected.wait(5)


def test_unreachable_ollama_reports_connection_error(service, monkeypatch):
    svc, _fake, events = service
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:9")
    svc.start({"action_id": "summarize", "material": "x"})
    _, finished = _until_finished(events)
    assert finished["status"] == "failed" and finished["error_code"] == "connection"


def test_http_error_is_reported(service):
    svc, fake, events = service
    fake.enqueue(status=500, chunks=[json.dumps({"error": "boom"}).encode()])
    svc.start({"action_id": "rewrite", "material": "x"})
    _, finished = _until_finished(events)
    assert finished["status"] == "failed" and finished["error_code"] == "http"


def test_unknown_action_and_empty_material_are_rejected(service):
    from ai_desktop.core.protocol import ProtocolError

    svc, _fake, _events = service
    for params in ({"action_id": "nope", "material": "x"}, {"action_id": "translate", "material": "  "}):
        with pytest.raises(ProtocolError):
            svc.start(params)
    assert {a["id"] for a in svc.list_actions({})["actions"]} >= {"translate", "explain"}
