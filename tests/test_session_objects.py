import json

from ai_desktop.services.session_objects import SessionObjects

NOTE = "x-coredata://AAA/ICNote/p1"
REMINDER = "x-apple-reminder://R1"


def created(objects, tool, object_id, title):
    objects.observe(tool, json.dumps({"title": title}), json.dumps({"result": "ok", "id": object_id,
                                                                  "title": title}), False)


def test_created_objects_are_listed_newest_first_with_ids():
    objects = SessionObjects()
    created(objects, "create_note", NOTE, "录制课程")
    created(objects, "create_reminder", REMINDER, "交周报")
    text = objects.render()
    assert text.index("交周报") < text.index("录制课程")
    assert f"id={NOTE}" in text and "刚创建" in text and "不是指令" in text


def test_later_results_update_the_same_object_and_failures_are_ignored():
    objects = SessionObjects()
    created(objects, "create_note", NOTE, "录制课程")
    objects.observe("delete_note", json.dumps({"id": NOTE}), json.dumps({"result": "ok", "title": "录制课程"}), False)
    assert objects.render().count("录制课程") == 1 and "已删除" in objects.render()
    objects.observe("complete_reminder", json.dumps({"id": REMINDER}), "无法完成", True)
    objects.observe("list_notes", "{}", "{}", False)
    objects.observe("complete_reminder", json.dumps({"id": REMINDER}), "not json", False)
    assert REMINDER not in objects.render()


def test_empty_and_bounded():
    objects = SessionObjects()
    assert objects.render() == ""
    for index in range(20):
        created(objects, "create_note", f"x-coredata://n{index}", "t" * 200)
    assert objects.render().count("id=") == SessionObjects.LIMIT
    assert "t" * 61 not in objects.render()


def test_worker_appends_the_session_context_only_to_tool_runs(qtbot, monkeypatch):
    from ai_desktop import config
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", True)
    from ai_desktop.llm.run_worker import RunWorker
    from ai_desktop.utils.storage import Message
    note = "[本会话已操作的对象] 笔记「x」"
    messages = [Message("user", "hi")]
    with_tools = RunWorker(messages, "sys", "m", agent_id="general_assistant", tools_admitted=True,
                           native_tools=True, session_context=note,
                           options={"num_ctx": 16384, "num_predict": 1024})
    assert note in with_tools.request.system_prompt
    plain = RunWorker(messages, "sys", "m", agent_id="general_assistant", session_context=note)
    assert note not in plain.request.system_prompt


def test_appended_notes_are_remembered_by_the_id_in_the_arguments():
    objects = SessionObjects()
    objects.observe("append_note", json.dumps({"id": NOTE, "text": "x"}),
                    json.dumps({"result": "ok", "title": "录制课程"}), False)
    assert f"id={NOTE}" in objects.render() and "已追加内容" in objects.render()
