"""The general assistant sends every message through the tool path; any failure degrades to plain chat."""
import pytest

from ai_desktop import config
from ai_desktop.desktop.tools import DesktopToolbox
from ai_desktop.services import audit_store
from ai_desktop.services.task_admission import TASK_MODEL, TaskAuthorization
from tests.test_task_entry import answer, bounded_task_fixture, controller, discovery, enqueue_discovery, idle

__all__ = ["bounded_task_fixture", "controller"]  # fixtures re-exported for pytest

NATIVE = list(DesktopToolbox().executors())


@pytest.fixture(autouse=True)
def native_tools_on(monkeypatch):
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 16384)  # room for every native tool


def use_agent(controller, agent_id):
    controller._model = TASK_MODEL
    controller._dialog.refresh_models([TASK_MODEL])
    agent = next(agent for agent in controller._all_agents if agent.id == agent_id)
    controller._active_agent = agent
    controller._dialog.set_active_agent(agent)
    controller._sync_action_context()


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
    assert controller._dialog._task_btn.text() == "命令与搜索：关闭"


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


def test_ordinary_messages_also_take_the_tool_path_and_read_like_plain_replies(qtbot, controller, ollama_server):
    use_agent(controller, "general_assistant")
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("你好！"))
    controller._on_user_message("你好")
    idle(qtbot, controller)
    assert [r["path"] for r in ollama_server.requests][-1] == "/api/chat"
    assert [t["function"]["name"] for t in ollama_server.requests[-1]["payload"]["tools"]] == NATIVE
    card = next(iter(controller._dialog._task_model_cards.values()))
    assert card.isHidden() is False and card.header.isHidden() and card.status.isHidden()
    assert card.regenerate.text() == "重新生成"


def test_every_role_shares_the_same_native_tools(qtbot, controller, ollama_server):
    use_agent(controller, "translator")
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("ok"))
    controller._on_user_message("打开备忘录")
    idle(qtbot, controller)
    sent = ollama_server.requests[-1]["payload"]
    assert [tool["function"]["name"] for tool in sent["tools"]] == NATIVE


def test_images_stay_on_plain_chat(qtbot, controller, ollama_server, tmp_path):
    from PyQt5.QtGui import QImage
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(0xffffff)
    path = tmp_path / "x.png"
    assert image.save(str(path))
    use_agent(controller, "general_assistant")
    controller._dialog.attach_image_paths([str(path)])
    ollama_server.enqueue(answer("看到了"))
    controller._on_user_message("看看这张图", images=controller._dialog.get_pending_images())
    idle(qtbot, controller)
    assert "tools" not in ollama_server.requests[-1]["payload"]


def test_regenerating_a_native_answer_uses_the_tool_path_again(qtbot, controller, ollama_server):
    use_agent(controller, "general_assistant")
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("第一次"))
    controller._on_user_message("你好")
    idle(qtbot, controller)
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("第二次"))
    controller._on_regenerate_requested()
    idle(qtbot, controller)
    assert [r["path"] for r in ollama_server.requests][-4:] == ["/api/version", "/api/tags", "/api/show", "/api/chat"]
    assert "tools" in ollama_server.requests[-1]["payload"]
    assert controller._messages[-1].content == "第二次"


def test_regeneration_falls_back_to_plain_chat_when_the_check_fails(qtbot, controller, ollama_server, monkeypatch):
    use_agent(controller, "general_assistant")
    enqueue_discovery(ollama_server)
    ollama_server.enqueue(answer("第一次"))
    controller._on_user_message("你好")
    idle(qtbot, controller)
    notices = []
    monkeypatch.setattr(controller, "_show_notice", lambda *args: notices.append(args))
    version, tags, show = discovery()
    show["capabilities"].remove("tools")
    enqueue_discovery(ollama_server, (version, tags, show))
    ollama_server.enqueue(answer("第二次"))
    controller._on_regenerate_requested()
    idle(qtbot, controller)
    assert "tools" not in ollama_server.requests[-1]["payload"]
    assert controller._messages[-1].content == "第二次"
    assert not [n for n in notices if "启用工具" in n[1] or "工具任务未启动" in n[1]]


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


def test_button_names_only_commands_and_search_and_the_caption_says_native_tools_need_no_setup(
        qtbot, controller):
    use_agent(controller, "general_assistant")
    dialog = controller._dialog
    dialog.set_task_authorization(None)
    assert dialog._task_btn.text() == "命令与搜索：关闭"
    assert "无需开启" in dialog._task_status.text() and not dialog._task_status.isHidden()
    assert "本机操作" in dialog._task_btn.toolTip()
