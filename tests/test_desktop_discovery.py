import pytest

from ai_desktop.desktop import opener, resolver, search
from ai_desktop.desktop.runner import MDFIND, OPEN


class FakeRunner:
    """Records argv and answers mdfind queries by predicate content; never runs a process."""

    def __init__(self, apps=(), files=(), fail=False):
        self.calls, self.apps, self.files, self.fail = [], list(apps), list(files), fail

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.fail:
            raise OSError("boom")
        if argv[0] == MDFIND:
            return "\n".join(self.apps if search.APP_TYPE in argv[-1] and "== " in argv[-1]
                             and "!=" not in argv[-1] else self.files)
        return ""


def test_find_apps_builds_one_query_over_all_roots_and_names():
    runner = FakeRunner(apps=["/System/Applications/Notes.app", "/Applications/WeChat.app", "/tmp/not-an-app"])
    found = search.find_apps("备忘录", runner=runner)
    assert [(c.kind, c.name) for c in found] == [("app", "Notes"), ("app", "WeChat")]
    argv = runner.calls[0]
    assert argv[0] == MDFIND and argv.count("-onlyin") == 4
    assert "kMDItemDisplayName" in argv[-1] and "kMDItemAlternateNames" in argv[-1] and "备忘录" in argv[-1]


def test_query_is_escaped_and_bounded():
    runner = FakeRunner()
    search.find_files('a"b*c\\d', runner=runner)
    assert 'a\\"b\\*c\\\\d' in runner.calls[0][-1]
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ValueError):
            search.find_files(bad, runner=runner)


def test_find_files_skips_relative_paths_and_bundles(tmp_path):
    folder = tmp_path / "项目"
    folder.mkdir()
    doc = tmp_path / "方案.docx"
    doc.write_text("x")
    runner = FakeRunner(files=[str(folder), str(doc), "relative/path", "/Applications/X.app"])
    found = search.find_files("方案", runner=runner, roots=[str(tmp_path)])
    assert [(c.kind, c.name) for c in found] == [("folder", "项目"), ("file", "方案.docx")]
    assert runner.calls[0][1:3] == ["-onlyin", str(tmp_path)]


def test_short_query_detection():
    assert resolver.is_short_query("微信") and resolver.is_short_query("产品方案 v2")
    assert not resolver.is_short_query("")
    assert not resolver.is_short_query("把下载文件夹里的 PDF 整理一下")
    assert not resolver.is_short_query("what is this?")
    assert not resolver.is_short_query("a\nb")


def test_resolve_ranks_exact_then_prefix_and_apps_before_files(tmp_path):
    doc = tmp_path / "Notes plan.txt"
    doc.write_text("x")
    runner = FakeRunner(apps=["/Applications/Notes Helper.app", "/System/Applications/Notes.app"],
                        files=[str(doc)])
    found = resolver.resolve("notes", runner=runner)
    assert [c.name for c in found] == ["Notes", "Notes Helper", "Notes plan.txt"]


def test_resolve_ignores_sentences_and_survives_lookup_failure():
    runner = FakeRunner(fail=True)
    assert resolver.resolve("请帮我整理一下下载文件夹。", runner=runner) == [] and runner.calls == []
    assert resolver.resolve("微信", runner=runner) == []


def test_resolve_dedupes_by_path(tmp_path):
    doc = tmp_path / "a.txt"
    doc.write_text("x")
    runner = FakeRunner(files=[str(doc), str(doc)])
    assert len(resolver.resolve("a", runner=runner)) == 1


def test_open_uses_open_for_documents_and_never_executes_scripts(tmp_path):
    doc, script, pkg = tmp_path / "a.pdf", tmp_path / "run.command", tmp_path / "x.pkg"
    for path in (doc, script, pkg):
        path.write_text("x")
    runner = FakeRunner()
    assert opener.open_path(str(doc), runner=runner) == "open"
    assert runner.calls[-1] == [OPEN, str(doc)]
    for risky in (script, pkg):
        assert opener.open_path(str(risky), runner=runner) == "reveal"
        assert runner.calls[-1] == [OPEN, "-R", str(risky)]


def test_executable_files_are_revealed_not_opened(tmp_path):
    binary = tmp_path / "tool"
    binary.write_text("x")
    binary.chmod(0o755)
    assert opener.open_action(str(binary)) == "reveal"


def test_open_rejects_bad_paths():
    runner = FakeRunner()
    for bad in ("relative.txt", "/definitely/missing/file", "/tmp/\x00x", None):
        with pytest.raises(ValueError):
            opener.open_path(bad, runner=runner)
    assert runner.calls == []


def test_only_apps_in_standard_locations_are_launched(tmp_path, monkeypatch):
    app = tmp_path / "Demo.app"
    app.mkdir()
    runner = FakeRunner()
    assert opener.open_path(str(app), runner=runner) == "reveal"
    monkeypatch.setattr(opener, "app_roots", lambda: (str(tmp_path),))
    assert opener.open_path(str(app), runner=runner) == "open"
    assert runner.calls[-1] == [OPEN, str(app)]
