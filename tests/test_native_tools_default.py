"""Open/locate requests get the desktop tools without setup; any failure degrades to plain chat."""
import pytest

from ai_desktop import config
from ai_desktop.desktop.resolver import wants_desktop_action
from ai_desktop.services import audit_store
from ai_desktop.services.task_admission import TASK_MODEL, TaskAuthorization
from tests.test_task_entry import answer, bounded_task_fixture, controller, discovery, enqueue_discovery, idle

__all__ = ["bounded_task_fixture", "controller"]  # fixtures re-exported for pytest

NATIVE = ["find_apps", "find_files", "open_path"]


@pytest.fixture(autouse=True)
def native_tools_on(monkeypatch):
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", True)


def use_agent(controller, agent_id):
    controller._model = TASK_MODEL
    controller._dialog.refresh_models([TASK_MODEL])
    agent = next(agent for agent in controller._all_agents if agent.id == agent_id)
    controller._active_agent = agent
    controller._dialog.set_active_agent(agent)
    controller._sync_action_context()


@pytest.mark.parametrize("text", ["打开备忘录", "帮我打开微信", "启动 Safari", "open Notes", "找一下我的产品方案文档",
                                  "查找下载文件夹里的 PDF", "Find the report app"])
def test_requests_that_open_or_locate_things_are_recognised(text):
    assert wants_desktop_action(text)


@pytest.mark.parametrize("text", ["", "你好", "解释一下这段代码", "找个笑话", "上网找一下最新新闻",
                                  "在网上搜索 Python 文档", "把这段话翻译成英文", "打开\n第二行",
                                  "打开" + "很长" * 40])
def test_other_messages_are_not(text):
    assert not wants_desktop_action(text)


def test_open_request_runs_with_native_tools_and_leaves_settings_alone(qtbot, controller, ollama_server):
    use_agent(controller, "general_assistant")
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("好的"))
    controller._on_user_message("打开备忘录")
    idle(qtbot, controller)
    assert [r["path"] for r in ollama_server.requests] == ["/api/version", "/api/tags", "/api/show", "/api/chat"]
    sent = ollama_server.requests[-1]["payload"]
    assert [tool["function"]["name"] for tool in sent["tools"]] == NATIVE
    run = audit_store.list_runs(controller._convo_id)[0]
    assert run["config"]["allowed_tools"] == NATIVE
    assert controller._task_authorization is None
    assert controller._dialog._task_btn.text() == "工具：关闭"


def test_failed_capability_check_falls_back_to_plain_chat(qtbot, controller, ollama_server, monkeypatch):
    use_agent(controller, "general_assistant")
    notices = []
    monkeypatch.setattr(controller, "_show_notice", lambda *args: notices.append(args))
    version, tags, show = discovery()
    show["capabilities"].remove("tools")
    enqueue_discovery(ollama_server, (version, tags, show))
    ollama_server.enqueue(answer("我没有工具，但可以回答。"))
    controller._on_user_message("打开备忘录")
    idle(qtbot, controller)
    assert "tools" not in ollama_server.requests[-1]["payload"]
    assert ollama_server.requests[-1]["path"] == "/api/chat"
    assert not [notice for notice in notices if "工具任务未启动" in notice]
    assert audit_store.list_runs(controller._convo_id)[0]["config"]["allowed_tools"] == []
    assert controller._dialog._input.toPlainText() == ""


@pytest.mark.parametrize("agent_id,text", [("translator", "打开备忘录"), ("general_assistant", "你好")])
def test_no_check_or_tools_for_other_roles_and_ordinary_messages(qtbot, controller, ollama_server, agent_id, text):
    use_agent(controller, agent_id)
    ollama_server.enqueue(answer("ok"))
    controller._on_user_message(text)
    idle(qtbot, controller)
    assert [r["path"] for r in ollama_server.requests] == ["/api/chat"]
    assert "tools" not in ollama_server.requests[0]["payload"]


def test_global_tool_switch_disables_implicit_tools(qtbot, controller, ollama_server, monkeypatch):
    monkeypatch.setattr(config, "CHAT_TOOLS_ENABLED", False)
    use_agent(controller, "general_assistant")
    ollama_server.enqueue(answer("ok"))
    controller._on_user_message("打开备忘录")
    idle(qtbot, controller)
    assert [r["path"] for r in ollama_server.requests] == ["/api/chat"]


def test_native_authorization_is_valid_alone_and_never_saved():
    grant = TaskAuthorization(agent_id="general_assistant", native=True)
    assert grant.record()["native"] is True
    with pytest.raises(ValueError):
        TaskAuthorization(agent_id="general_assistant")
