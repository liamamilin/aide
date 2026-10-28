"""Workspace conversation methods: persistence parity with the Qt app, streaming send, busy guard."""
import queue

import pytest

from ai_desktop import config
from ai_desktop.core.conversations import ConversationService, collapse_versions
from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.runs import RunService
from ai_desktop.utils import storage
from tests.fake_ollama import FakeOllama


def _frame(content="", done=False, **extra):
    return {"message": {"role": "assistant", "content": content}, "done": done, **extra}


@pytest.fixture
def env(tmp_db, monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", fake.url)
    events: queue.Queue = queue.Queue()
    runs = RunService(lambda name, data: events.put((name, data)))
    yield ConversationService(runs), fake, events
    fake.close()


def _finished(events, timeout=10):
    while True:
        name, data = events.get(timeout=timeout)
        if name == "run.finished":
            return data


def test_send_persists_both_sides_titles_and_streams(env):
    chats, fake, events = env
    convo_id = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_frame("Hi "), _frame("there"), _frame("", done=True, done_reason="stop"))
    started = chats.send({"conversation_id": convo_id, "text": "  打个招呼  "})
    finished = _finished(events)
    assert finished["run_id"] == started["run_id"] and finished["conversation_id"] == convo_id
    assert finished["status"] == "succeeded"
    got = chats.get({"id": convo_id})
    assert [(m["role"], m["content"]) for m in got["messages"]] == [("user", "  打个招呼  "), ("assistant", "Hi there")]
    assert got["title"] == "打个招呼"
    sent = fake.requests[0]["payload"]["messages"]
    assert sent[0]["role"] == "system" and sent[-1]["content"] == "  打个招呼  "


def test_second_turn_sends_history_and_busy_guard(env):
    chats, fake, events = env
    convo_id = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_frame("one"), _frame("", done=True, done_reason="stop"))
    chats.send({"conversation_id": convo_id, "text": "first"})
    _finished(events)
    scenario = fake.enqueue(_frame("two"), _frame("", done=True, done_reason="stop"), hold_open=True)
    chats.send({"conversation_id": convo_id, "text": "second"})
    scenario.received.wait(5)
    with pytest.raises(ProtocolError):
        chats.send({"conversation_id": convo_id, "text": "third"})
    roles = [m["role"] for m in fake.requests[1]["payload"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_failed_run_saves_no_assistant_message(env):
    chats, fake, events = env
    convo_id = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(status=500, chunks=[b'{"error":"boom"}'])
    chats.send({"conversation_id": convo_id, "text": "hi"})
    assert _finished(events)["status"] == "failed"
    assert [m["role"] for m in chats.get({"id": convo_id})["messages"]] == ["user"]
    # the conversation is usable again afterwards
    fake.enqueue(_frame("ok"), _frame("", done=True, done_reason="stop"))
    chats.send({"conversation_id": convo_id, "text": "again"})
    assert _finished(events)["status"] == "succeeded"


def test_collapse_prefers_active_generation(tmp_db):
    convo = storage.create_conversation("general_assistant")
    user = storage.save_message(convo.id, "user", "q")
    old = storage.save_message(convo.id, "assistant", "old")
    new = storage.save_message(convo.id, "assistant", "new")
    storage.save_generation(user.id, "r1", status="succeeded", answer="old", assistant_message_id=old.id, active=True)
    assert [m["content"] for m in collapse_versions(storage.get_conversation(convo.id).messages)] == ["q", "old"]
    storage.save_generation(user.id, "r2", status="succeeded", answer="new", assistant_message_id=new.id, active=True)
    assert [m["content"] for m in collapse_versions(storage.get_conversation(convo.id).messages)] == ["q", "new"]


def test_create_seed_list_rename_delete(env):
    chats, _fake, _events = env
    created = chats.create({"agent_id": "translator", "messages": [
        {"role": "user", "content": "你好"}, {"role": "assistant", "content": "Hello"}]})
    got = chats.get({"id": created["id"]})
    assert [m["content"] for m in got["messages"]] == ["你好", "Hello"] and got["title"] == "你好"
    listed = chats.list({})["conversations"]
    assert listed[0]["id"] == created["id"] and listed[0]["message_count"] == 2
    assert chats.rename({"id": created["id"], "title": "新标题"})["title"] == "新标题"
    chats.delete({"id": created["id"]})
    with pytest.raises(ProtocolError):
        chats.get({"id": created["id"]})
    assert {a["id"] for a in chats.agents({})["agents"]} >= {"general_assistant", "translator"}
