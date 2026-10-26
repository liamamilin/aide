import json
import threading
from types import SimpleNamespace

from ai_desktop import config
from ai_desktop.desktop.runner import MDFIND, OPEN
from ai_desktop.desktop.tools import DesktopToolbox
from ai_desktop.utils.storage import Message


def _context(cancelled=False):
    event = threading.Event()
    if cancelled:
        event.set()
    return SimpleNamespace(cancelled=event)


class Runner:
    def __init__(self, out=""):
        self.calls, self.out = [], out

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.out


def test_find_apps_returns_metadata_only():
    toolbox = DesktopToolbox(Runner("/System/Applications/Notes.app\n"))
    out = toolbox.find_apps({"query": "备忘录"}, _context())
    assert not out.error
    assert json.loads(out.text) == {"results": [{"kind": "app", "name": "Notes",
                                                   "path": "/System/Applications/Notes.app"}]}


def test_lookup_failure_is_a_finite_error():
    def boom(argv):
        raise OSError("secret detail")
    out = DesktopToolbox(boom).find_files({"query": "x"}, _context())
    assert out.error and "secret" not in out.text


def test_cancelled_calls_do_nothing():
    runner = Runner()
    toolbox = DesktopToolbox(runner)
    assert toolbox.find_apps({"query": "x"}, _context(True)).error
    assert toolbox.open_path({"path": "/tmp"}, _context(True)).error
    assert runner.calls == []


def test_open_path_opens_or_reveals_and_rejects_missing(tmp_path):
    doc, script = tmp_path / "a.pdf", tmp_path / "x.command"
    doc.write_text("x")
    script.write_text("x")
    runner = Runner()
    toolbox = DesktopToolbox(runner)
    assert json.loads(toolbox.open_path({"path": str(doc)}, _context()).text) == {"action": "open"}
    assert runner.calls[-1] == [OPEN, str(doc)]
    assert json.loads(toolbox.open_path({"path": str(script)}, _context()).text) == {"action": "reveal"}
    assert runner.calls[-1] == [OPEN, "-R", str(script)]
    missing = toolbox.open_path({"path": str(tmp_path / "gone")}, _context())
    assert missing.error and len(runner.calls) == 2
    assert MDFIND not in [call[0] for call in runner.calls]


def test_worker_grants_desktop_tools_only_in_admitted_runs(qtbot, monkeypatch):
    from ai_desktop.llm.run_worker import RunWorker
    from ai_desktop.services.web_search import SearchSettings
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", True)
    search = SearchSettings.from_config()
    kwargs = dict(agent_id="general_assistant", search_settings=search,
                  options={"num_ctx": 16384, "num_predict": 1024})
    messages = [Message('user', 'hi')]
    admitted = RunWorker(messages, "sys", "m", tools_admitted=True, **kwargs)
    assert [t.name for t in admitted.context.tools] == ["web_search", *DesktopToolbox().executors()]
    assert "find_apps" in admitted.request.system_prompt
    plain = RunWorker(messages, "sys", "m", agent_id="general_assistant")
    assert plain.context.tools == ()
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", False)
    assert [t.name for t in RunWorker(messages, "sys", "m", tools_admitted=True, **kwargs).context.tools] == [
        "web_search"]


class Scripts:
    """Records osascript argv; never runs AppleScript."""

    def __init__(self, out=""):
        self.calls, self.out = [], out

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.out


def test_note_text_is_escaped_html_passed_as_an_argument():
    from ai_desktop.integrations import apple
    runner = Scripts()
    toolbox = DesktopToolbox(runner)
    body = "第一行\n\n第二行 & 'q'\"; do shell script \"x"
    out = toolbox.create_note({"title": "想法 <b>", "body": body}, _context())
    assert not out.error
    osascript, flag, script, argument = runner.calls[0]
    assert osascript == "/usr/bin/osascript" and flag == "-e"
    assert "do shell script" not in script and "<b>" not in script
    assert "&lt;b&gt;" in argument and "&amp;" in argument and "<div><br></div>" in argument
    assert apple.note_html("t", "") == "<div><b>t</b></div>"


def test_reminder_passes_title_and_notes_as_arguments():
    runner = Scripts()
    out = DesktopToolbox(runner).create_reminder({"title": "交周报", "notes": "周五前"}, _context())
    assert not out.error and runner.calls[0][-2:] == ["交周报", "周五前"]


def test_script_failures_and_bad_input_are_finite_errors():
    def boom(argv):
        raise OSError("-1743 not authorized")
    toolbox = DesktopToolbox(boom)
    for out in (toolbox.create_note({"title": "t"}, _context()), toolbox.create_reminder({"title": "t"}, _context()),
                toolbox.arrange_window({"app": "Safari", "layout": "maximize"}, _context())):
        assert out.error and "1743" not in out.text
    runner = Scripts()
    bad = DesktopToolbox(runner).arrange_window({"app": "Safari", "layout": "diagonal"}, _context())
    assert bad.error and runner.calls == []


def test_window_listing_parses_and_explains_an_empty_result():
    listed = DesktopToolbox(Scripts("Safari\t首页\nNotes\t备忘录\nbad line\n")).list_windows({}, _context())
    assert json.loads(listed.text)["windows"][0] == {"app": "Safari", "title": "首页"}
    empty = json.loads(DesktopToolbox(Scripts("")).list_windows({}, _context()).text)
    assert empty["windows"] == [] and "辅助功能" in empty["note"]


def test_arrange_window_sends_app_and_layout_as_arguments():
    runner = Scripts()
    assert not DesktopToolbox(runner).arrange_window({"app": "Safari", "layout": "left_half"}, _context()).error
    assert runner.calls[0][-2:] == ["Safari", "left_half"]


SEP, REC = "\x1f", "\x1e"
NOTE_A, NOTE_B = "x-coredata://AAA/ICNote/p1", "x-coredata://AAA/ICNote/p2"


def _listing():
    return (f"{NOTE_B}{SEP}较旧的想法{SEP}86400.0{REC}\n{NOTE_A}{SEP}录制课程{SEP}30{REC}\n"
            f"junk line{REC}bad{SEP}x{SEP}1{REC}")


def test_list_notes_counts_all_and_returns_newest_first_without_bodies():
    runner = Scripts(_listing())
    out = DesktopToolbox(runner).list_notes({}, _context())
    record = json.loads(out.text)
    assert record["total_notes"] == 2 and record["matching"] == 2
    assert [n["title"] for n in record["notes"]] == ["录制课程", "较旧的想法"]
    assert record["notes"][0] == {"id": NOTE_A, "title": "录制课程", "age_seconds": 30}
    assert "body" not in runner.calls[0][2].split("every note")[0]


def test_list_notes_filters_and_limits_but_keeps_the_total():
    toolbox = DesktopToolbox(Scripts(_listing()))
    record = json.loads(toolbox.list_notes({"query": "课程", "limit": 1}, _context()).text)
    assert record["total_notes"] == 2 and record["matching"] == 1 and len(record["notes"]) == 1
    assert toolbox.list_notes({}, _context(True)).error


def test_create_note_returns_the_id_for_later_reference():
    out = DesktopToolbox(Scripts(NOTE_A + "\n")).create_note({"title": "录制课程"}, _context())
    assert json.loads(out.text) == {"result": "已在备忘录中新建笔记。", "id": NOTE_A, "title": "录制课程"}


class Approver:
    """A broker whose 'UI' answers immediately and remembers what the user was shown."""

    def __init__(self, approve):
        from ai_desktop.services.command_confirmation import ConfirmationBroker
        self.shown = []
        self.broker = ConfirmationBroker(self._ask)
        self.approve = approve

    def _ask(self, request):
        self.shown.append(request)
        threading.Thread(target=lambda: self.broker.respond(request, self.approve)).start()


def _action_context():
    request = SimpleNamespace(run_id="r", step_id="s", request_id="q", conversation_id=1)
    return SimpleNamespace(cancelled=threading.Event(), request=request, on_state=None, budget=None,
                           call=SimpleNamespace(local_call_id="c1"))


class TitleThenAck(Scripts):
    def __call__(self, argv):
        self.calls.append(list(argv))
        return "录制课程\n" if "return name of note id" in argv[2] else ""


def test_delete_note_shows_the_title_and_deletes_only_after_approval():
    approver, runner = Approver(True), TitleThenAck()
    out = DesktopToolbox(runner, approver.broker).delete_note({"id": NOTE_A}, _action_context())
    assert not out.error and "最近删除" in out.text
    assert [r.command for r in approver.shown] == ["删除备忘录：录制课程"]
    assert "delete note id" in runner.calls[-1][2] and runner.calls[-1][-1] == NOTE_A


def test_delete_note_does_nothing_when_denied():
    approver, runner = Approver(False), TitleThenAck()
    out = DesktopToolbox(runner, approver.broker).delete_note({"id": NOTE_A}, _action_context())
    assert out.error and "拒绝" in out.text
    assert not any("delete note id" in call[2] for call in runner.calls)


def test_destructive_tools_fail_closed_without_a_confirmation_channel():
    runner = TitleThenAck()
    out = DesktopToolbox(runner).executors()["delete_note"]({"id": NOTE_A}, _action_context())
    assert out.error and runner.calls == []


def test_delete_note_rejects_a_guessed_id_before_touching_notes():
    runner = TitleThenAck()
    out = DesktopToolbox(runner, Approver(True).broker).delete_note({"id": "not-an-id"}, _action_context())
    assert out.error and runner.calls == []


def test_list_reminders_counts_open_items_and_filters():
    runner = Scripts(f"r1{SEP}交周报{REC}\nr2{SEP}买牛奶{REC}")
    toolbox = DesktopToolbox(runner)
    record = json.loads(toolbox.list_reminders({}, _context()).text)
    assert record["total_open_reminders"] == 2 and record["reminders"][0] == {"id": "r1", "title": "交周报"}
    assert "Reminders" in runner.calls[0][2]
    filtered = json.loads(toolbox.list_reminders({"query": "牛奶"}, _context()).text)
    assert filtered["matching"] == 1 and filtered["total_open_reminders"] == 2


REMINDER = "x-apple-reminder://3452D796-78BC-4316-BA35-BA0C5C7D22AA"


class ReminderTitle(Scripts):
    def __call__(self, argv):
        self.calls.append(list(argv))
        return "明天要录制课程\n" if "return name of reminder id" in argv[2] else ""


def test_complete_reminder_marks_it_done_without_asking():
    runner = ReminderTitle()
    out = DesktopToolbox(runner).complete_reminder({"id": REMINDER}, _context())
    assert json.loads(out.text) == {"result": "已标记为完成。", "title": "明天要录制课程"}
    assert "set completed of reminder id" in runner.calls[-1][2] and runner.calls[-1][-1] == REMINDER


def test_delete_reminder_warns_it_is_permanent_and_needs_approval():
    approver, runner = Approver(True), ReminderTitle()
    out = DesktopToolbox(runner, approver.broker).delete_reminder({"id": REMINDER}, _action_context())
    assert not out.error and "delete reminder id" in runner.calls[-1][2]
    shown = approver.shown[0]
    assert shown.command == "永久删除提醒：明天要录制课程" and "无法恢复" in shown.reason
    denied, runner = Approver(False), ReminderTitle()
    assert DesktopToolbox(runner, denied.broker).delete_reminder({"id": REMINDER}, _action_context()).error
    assert not any("delete reminder id" in call[2] for call in runner.calls)


def test_reminder_tools_reject_guessed_ids_and_fail_closed_without_confirmation():
    runner = ReminderTitle()
    toolbox = DesktopToolbox(runner)
    assert toolbox.complete_reminder({"id": NOTE_A}, _context()).error
    assert toolbox.executors()["delete_reminder"]({"id": REMINDER}, _action_context()).error
    assert runner.calls == []


def test_append_note_previews_the_added_text_and_only_adds_after_approval():
    approver, runner = Approver(True), TitleThenAck()
    out = DesktopToolbox(runner, approver.broker).append_note(
        {"id": NOTE_A, "text": "第二行 <b>&"}, _action_context())
    assert not out.error and json.loads(out.text)["title"] == "录制课程"
    assert approver.shown[0].command == "在备忘录《录制课程》末尾追加：\n第二行 <b>&"
    osascript, flag, script, note_id, added = runner.calls[-1]
    assert "set body of theNote to (body of theNote) &" in script and note_id == NOTE_A
    assert added == "<div>第二行 &lt;b&gt;&amp;</div>"


def test_append_note_does_nothing_when_denied_or_given_bad_input():
    denied, runner = Approver(False), TitleThenAck()
    toolbox = DesktopToolbox(runner, denied.broker)
    assert toolbox.append_note({"id": NOTE_A, "text": "x"}, _action_context()).error
    assert not any("set body" in call[2] for call in runner.calls)
    assert DesktopToolbox(TitleThenAck(), Approver(True).broker).append_note(
        {"id": "bad", "text": "x"}, _action_context()).error
