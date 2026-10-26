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
    assert [t.name for t in admitted.context.tools] == ["web_search", "find_apps", "find_files", "open_path"]
    assert "find_apps" in admitted.request.system_prompt
    plain = RunWorker(messages, "sys", "m", agent_id="general_assistant")
    assert plain.context.tools == ()
    monkeypatch.setattr(config, "DESKTOP_TOOLS_ENABLED", False)
    assert [t.name for t in RunWorker(messages, "sys", "m", tools_admitted=True, **kwargs).context.tools] == [
        "web_search"]
