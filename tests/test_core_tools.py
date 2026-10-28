"""Native desktop tools in the core: admission, confirmation round-trip, cancel, and the Qt-free invariant."""
import json
import queue
import sys

import pytest

from ai_desktop import config
from ai_desktop.core.conversations import ConversationService
from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.runs import RunService
from ai_desktop.llm.thinking import ThinkMode, ThinkSetting
from tests.fake_ollama import FakeOllama

NOTE_ID = "x-coredata://0000/ICNote/p1"


def _frame(content="", done=False, **extra):
    return {"message": {"role": "assistant", "content": content}, "done": done, **extra}


def _tool_turn(name, arguments):
    return {"message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": name, "arguments": arguments}}]},
            "done": True, "done_reason": "stop"}


def _final(text):
    return (_frame(text), _frame("", done=True, done_reason="stop"))


class Recorder:
    """Stands in for osascript/mdfind: answers from a table and records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        script = " ".join(argv)
        if "name of" in script or "title" in script.lower():
            return "周报草稿\n"
        return ""


@pytest.fixture
def env(tmp_db, monkeypatch):
    fake = FakeOllama()
    fake.static_responses["/api/version"] = b'{"version":"0.34.3"}'
    fake.static_responses["/api/tags"] = json.dumps({"models": [{"name": "m:1", "digest": "abc"}]}).encode()
    fake.static_responses["/api/show"] = json.dumps({
        "capabilities": ["completion", "tools"], "thinking": {"values": [False, True], "default": True}}).encode()
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", fake.url)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "m:1")
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 32768)
    monkeypatch.setattr(config, "OLLAMA_NUM_PREDICT", 1024)
    monkeypatch.setattr(config, "OLLAMA_THINK", ThinkSetting(ThinkMode.OFF))
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", True)
    monkeypatch.setattr(config, "CHAT_TOOLS_ENABLED", True)
    events: queue.Queue = queue.Queue()
    runs = RunService(lambda name, data: events.put((name, data)))
    runner = Recorder()
    runs.desktop_runner = runner
    chats = ConversationService(runs)
    yield chats, runs, fake, events, runner
    fake.close()


def _next(events, name, timeout=10):
    while True:
        got, data = events.get(timeout=timeout)
        if got == name:
            return data


def test_tool_send_asks_confirmation_runs_after_approval_and_stays_qt_free(env):
    chats, runs, fake, events, runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("delete_note", {"id": NOTE_ID}))
    fake.enqueue(*_final("已删除。"))
    started = chats.send({"conversation_id": convo, "text": "删掉那条笔记"})
    assert "delete_note" in started["tools"] and started["tools_note"] == ""

    request = _next(events, "run.confirm_request")
    assert request["run_id"] == started["run_id"] and "周报草稿" in request["preview"]
    assert not any("delete" in " ".join(call).lower() for call in runner.calls)   # nothing ran before approval
    runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": True})
    finished = _next(events, "run.finished")
    assert finished["status"] == "succeeded" and finished["text"] == "已删除。"
    assert any("delete" in " ".join(call).lower() for call in runner.calls)
    # the answer is saved; tool turns are not (same as the Qt app)
    assert [m["role"] for m in chats.get({"id": convo})["messages"]] == ["user", "assistant"]
    # a confirmation can be answered once only
    with pytest.raises(ProtocolError):
        runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": True})


def test_denied_confirmation_runs_nothing_and_tells_the_model(env):
    chats, runs, fake, events, runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("delete_note", {"id": NOTE_ID}))
    fake.enqueue(*_final("好的，没有删除。"))
    chats.send({"conversation_id": convo, "text": "删掉"})
    request = _next(events, "run.confirm_request")
    runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": False})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert not any("delete" in " ".join(call).lower() for call in runner.calls)
    tool_message = fake.requests[1]["payload"]["messages"][-1]
    assert tool_message["role"] == "tool" and "拒绝" in tool_message["content"]


def test_cancel_while_waiting_for_confirmation_unblocks_the_run(env):
    chats, runs, fake, events, runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("delete_note", {"id": NOTE_ID}))
    started = chats.send({"conversation_id": convo, "text": "删掉"})
    request = _next(events, "run.confirm_request")
    runs.cancel({"run_id": started["run_id"]})
    assert _next(events, "run.finished")["status"] == "cancelled"
    assert not any("delete" in " ".join(call).lower() for call in runner.calls)
    with pytest.raises(ProtocolError):
        runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": True})


def test_tool_progress_events_name_the_tool(env):
    chats, runs, fake, events, _runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("find_apps", {"query": "Safari"}))
    fake.enqueue(*_final("找到了。"))
    chats.send({"conversation_id": convo, "text": "找 Safari"})
    seen = []
    while True:
        name, data = events.get(timeout=10)
        if name == "run.tool":
            seen.append((data["tool"], data["state"]))
        if name == "run.finished":
            break
    assert ("find_apps", "tool_started") in seen and ("find_apps", "tool_finished") in seen


def test_remote_service_is_refused_for_tools_with_a_reason(env, monkeypatch):
    chats, runs, *_ = env
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://192.0.2.7:11434")
    agent = runs.agents[0]
    resolved = runs.resolve_model("m:1", agent_profile_id=None)
    admission, note = chats._admit_tools(agent, resolved)
    assert admission is None and "本机" in note and note.startswith("本次未启用工具")


def test_model_without_tool_capability_runs_tool_free_and_says_why(env):
    chats, _runs, fake, events, _runner = env
    fake.static_responses["/api/show"] = json.dumps({"capabilities": ["completion"]}).encode()
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(*_final("hello"))
    started = chats.send({"conversation_id": convo, "text": "hi"})
    assert started["tools"] == [] and "未声明工具调用能力" in started["tools_note"]
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert "tools" not in fake.requests[0]["payload"]


def test_quick_actions_stay_tool_free(env):
    _chats, runs, fake, events, _runner = env
    fake.enqueue(*_final("译文"))
    runs.start({"action_id": "translate", "material": "你好"})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert "tools" not in fake.requests[0]["payload"]


def test_core_process_imports_no_qt():
    import subprocess
    code = ("import sys; from ai_desktop.core import methods; methods.build_handlers(lambda *a: None);"
            "from ai_desktop.llm import tool_run; from ai_desktop.core import admission;"
            "print(any(m == 'PyQt5' or m.startswith('PyQt5.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr


def test_lookup_tools_forward_their_real_output_but_actions_do_not(env):
    chats, runs, fake, events, _runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("find_apps", {"query": "Safari"}))
    fake.enqueue(*_final("找到了。"))
    chats.send({"conversation_id": convo, "text": "找 Safari"})
    finished = []
    while True:
        name, data = events.get(timeout=10)
        if name == "run.tool" and data["state"] == "tool_finished":
            finished.append(data)
        if name == "run.finished":
            break
    assert finished and finished[0]["tool"] == "find_apps"
    assert "results" in finished[0]["output"]            # the tool's own JSON, not model prose


def test_reopened_conversation_carries_the_lookup_results_it_showed(env):
    chats, _runs, fake, events, _runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("find_apps", {"query": "Safari"}))
    fake.enqueue(*_final("找到了。"))
    chats.send({"conversation_id": convo, "text": "找 Safari"})
    assert _next(events, "run.finished")["status"] == "succeeded"
    answer = chats.get({"id": convo})["messages"][-1]
    assert answer["role"] == "assistant" and answer["tool_results"][0]["tool"] == "find_apps"
    assert "results" in answer["tool_results"][0]["output"]
