"""Opt-in Bash in the core: workspace rules, per-conversation switch, and confirmation before anything runs."""
# ruff: noqa: F811  (the imported `env` fixture is used as a test argument)
import json
import os

import pytest

from ai_desktop.core.protocol import ProtocolError
from ai_desktop.core.shell import ShellService
from tests.test_core_tools import _final, _next, _tool_turn, env  # noqa: F401


@pytest.fixture
def shell(tmp_db):
    return ShellService()


def test_workspace_must_be_a_real_specific_folder(shell, tmp_path):
    for bad in ("", "/", os.path.expanduser("~"), str(tmp_path / "missing")):
        with pytest.raises(ProtocolError):
            shell.set_workspace({"path": bad})
    assert shell.status({})["usable"] is False
    got = shell.set_workspace({"path": str(tmp_path)})
    assert got["usable"] is True and got["workspace"] == os.path.realpath(tmp_path)
    assert shell.set_workspace({"path": str(tmp_path), "policy": "confirm_all"})["policy"] == "confirm_all"
    with pytest.raises(ProtocolError):
        shell.set_workspace({"path": str(tmp_path), "policy": "yolo"})
    assert shell.clear_workspace({})["usable"] is False


def test_switch_needs_a_workspace_and_a_known_conversation(env, tmp_path):
    chats, *_ = env
    shell = chats._shell
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    with pytest.raises(ProtocolError):
        shell.set_conversation({"conversation_id": convo, "enabled": True})
    shell.set_workspace({"path": str(tmp_path)})
    shell.set_conversation({"conversation_id": convo, "enabled": True})
    assert chats.get({"id": convo})["bash"] is True
    shell.set_conversation({"conversation_id": convo, "enabled": False})
    assert chats.get({"id": convo})["bash"] is False
    with pytest.raises(ProtocolError):
        shell.set_conversation({"conversation_id": 9999, "enabled": True})


def test_bash_is_not_offered_unless_switched_on(env, tmp_path):
    chats, runs, fake, events, _runner = env
    chats._shell.set_workspace({"path": str(tmp_path)})
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(*_final("ok"))
    assert "bash" not in chats.send({"conversation_id": convo, "text": "hi"})["tools"]
    _next(events, "run.finished")
    chats._shell.set_conversation({"conversation_id": convo, "enabled": True})
    fake.enqueue(*_final("ok"))
    assert "bash" in chats.send({"conversation_id": convo, "text": "hi again"})["tools"]


def test_a_command_outside_the_safe_list_shows_folder_and_text_and_denial_runs_nothing(env, tmp_path):
    chats, runs, fake, events, _runner = env
    chats._shell.set_workspace({"path": str(tmp_path)})
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    chats._shell.set_conversation({"conversation_id": convo, "enabled": True})
    fake.enqueue(_tool_turn("bash", {"command": "touch made.txt"}))
    fake.enqueue(*_final("好的，没有执行。"))
    chats.send({"conversation_id": convo, "text": "建个文件"})
    request = _next(events, "run.confirm_request")
    assert os.path.realpath(tmp_path) in request["preview"] and "touch made.txt" in request["preview"]
    runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": False})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert not (tmp_path / "made.txt").exists()
    result = json.loads(fake.requests[1]["payload"]["messages"][-1]["content"])
    assert result["error_type"] == "confirmation_denied"


def test_stop_while_waiting_for_approval_runs_nothing(env, tmp_path):
    chats, runs, fake, events, _runner = env
    chats._shell.set_workspace({"path": str(tmp_path)})
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    chats._shell.set_conversation({"conversation_id": convo, "enabled": True})
    fake.enqueue(_tool_turn("bash", {"command": "touch made.txt"}))
    started = chats.send({"conversation_id": convo, "text": "建个文件"})
    _next(events, "run.confirm_request")
    runs.cancel({"run_id": started["run_id"]})
    assert _next(events, "run.finished")["status"] == "cancelled"
    assert not (tmp_path / "made.txt").exists()


def test_folder_gone_after_switching_on_runs_without_bash_and_says_why(env, tmp_path):
    chats, runs, fake, events, _runner = env
    work = tmp_path / "proj"
    work.mkdir()
    chats._shell.set_workspace({"path": str(work)})
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    chats._shell.set_conversation({"conversation_id": convo, "enabled": True})
    work.rmdir()
    fake.enqueue(*_final("ok"))
    started = chats.send({"conversation_id": convo, "text": "hi"})
    assert "bash" not in started["tools"] and "文件夹" in started["tools_note"]
