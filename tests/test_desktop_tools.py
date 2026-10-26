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
    kwargs = dict(agent_id="general_assistant", search_settings=search)
    messages = [Message('user', 'hi')]
    admitted = RunWorker(messages, "sys", "m", tools_admitted=True, **kwargs)
    assert [t.name for t in admitted.context.tools] == [
        "web_search", "find_apps", "find_files", "open_path", "list_windows", "arrange_window", "create_note",
        "create_reminder"]
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
