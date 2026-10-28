import json

from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput
from ai_desktop.ui import native_tool_view as view
from ai_desktop.ui.tool_card import ToolCard


def started(tool, **args):
    return RunEvent("run", 1, "step", "request", 1, RunEventKind.TOOL_STARTED, "call", tool_name=tool,
                    arguments_json=json.dumps(args))


def test_every_registered_native_tool_has_a_plain_description():
    from ai_desktop.desktop.tools import DesktopToolbox
    assert set(DesktopToolbox().executors()) == view.NATIVE_TOOLS
    for name in view.NATIVE_TOOLS:
        assert view.describe_call(name, {"query": "x", "path": "/a/b.pdf", "app": "Safari", "layout": "left_half",
                                         "title": "t", "text": "x"})


def test_calls_read_like_sentences_not_arguments():
    assert view.describe_call("open_path", {"path": "/p/视频制作项目", "app": "/Applications/Cursor.app"}) \
        == "打开：视频制作项目，用 Cursor"
    assert view.describe_call("arrange_window", {"app": "Safari", "layout": "left_half"}) == "Safari → 左半屏"
    days = view.describe_call("find_files", {"query": "方案", "modified_within_days": 7})
    assert days == "查找文件：方案（最近 7 天）"


def test_results_summarise_without_json():
    assert view.describe_result("find_files", json.dumps({"results": [1, 2, 3]}), False) == "找到 3 项"
    assert view.describe_result("find_apps", json.dumps({"results": []}), False) == "没有找到"
    assert view.describe_result("list_notes", json.dumps({"total_notes": 12, "notes": []}), False) == "共 12 条"
    assert view.describe_result("open_path", json.dumps({"action": "reveal"}), False) == "已在访达中显示"
    assert view.describe_result("delete_note", json.dumps({"title": "录制课程"}), False) == "已删除：录制课程"
    assert view.describe_result("create_note", "无法创建笔记；请允许控制备忘录。", True) == "未完成"


def test_permission_failures_point_to_the_right_settings_pane():
    assert view.permission_pane("无法创建笔记；请允许控制备忘录。") == view.AUTOMATION_PANE
    assert view.permission_pane("需要授予辅助功能权限") == view.ACCESSIBILITY_PANE
    assert view.permission_pane("找不到这条笔记") is None


def test_card_shows_a_sentence_then_a_status_and_a_settings_button_on_permission_errors(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr("ai_desktop.ui.tool_card.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    card = ToolCard(started("create_note", title="想法"))
    qtbot.addWidget(card)
    assert card.command_view.toPlainText() == "新建笔记：想法" and card.command_view.height() < 60
    card.update_event(RunEvent("run", 1, "step", "request", 2, RunEventKind.TOOL_FINISHED, "call",
                               tool_name="create_note", arguments_json="{}",
                               output=ToolOutput("无法创建笔记；请允许控制备忘录。", True)))
    assert card.status.text() == "未完成" and "允许控制" in card.reason.text()
    assert not card.permission_button.isHidden()
    card.permission_button.click()
    assert opened == [view.AUTOMATION_PANE]
    done = ToolCard(started("list_notes"))
    qtbot.addWidget(done)
    done.update_event(RunEvent("run", 1, "step", "request", 2, RunEventKind.TOOL_FINISHED, "call",
                               tool_name="list_notes", arguments_json="{}",
                               output=ToolOutput(json.dumps({"total_notes": 12, "notes": []}))))
    assert done.status.text() == "共 12 条" and done.permission_button.isHidden()
