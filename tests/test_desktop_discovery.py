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
    search.find_files('-x "y"', runner=runner)
    assert runner.calls[0][-2:] == ["-name", '-x "y"']  # a value of -name, never a separate option
    search.find_apps('a"b*c\\d', runner=runner)
    assert 'a\\"b\\*c\\\\d' in runner.calls[1][-1]
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
    assert sorted((c.kind, c.name) for c in found) == [("file", "方案.docx"), ("folder", "项目")]
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


def test_file_search_defaults_to_home_and_skips_library_hidden_and_build_folders(monkeypatch):
    monkeypatch.setattr(search.config, "FILE_SEARCH_ROOTS", ())
    assert search.default_file_roots() == (str(search.Path.home()),)
    monkeypatch.setattr(search.config, "FILE_SEARCH_ROOTS", ("/Volumes/Data", "relative"))
    assert search.default_file_roots() == ("/Volumes/Data",)
    paths = ["/Users/me/Documents/方案.docx", "/Users/me/Library/Caches/方案.db", "/Users/me/.config/方案",
             "/Users/me/app/node_modules/方案/index.js", "/Users/me/proj/.git/方案"]
    monkeypatch.setattr(search.os.path, "isdir", lambda path: False)
    found = search.find_files("方案", runner=lambda argv: "\n".join(paths), roots=["/Users/me"])
    assert [c.path for c in found] == ["/Users/me/Documents/方案.docx"]


def test_find_files_sorts_newest_first_filters_by_age_and_marks_projects(tmp_path):
    import os
    now = 2_000_000_000.0
    old, new, project = tmp_path / "方案-旧.docx", tmp_path / "方案-新.docx", tmp_path / "方案项目"
    for path in (old, new):
        path.write_text("x")
    project.mkdir()
    (project / ".git").mkdir()
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))
    os.utime(new, (now - 2 * 86400, now - 2 * 86400))
    os.utime(project, (now - 1 * 86400, now - 1 * 86400))
    runner = FakeRunner(files=[str(old), str(new), str(project)])
    found = search.find_files("方案", runner=runner, roots=[str(tmp_path)], now=now)
    assert [(c.kind, c.name) for c in found] == [("project", "方案项目"), ("file", "方案-新.docx"),
                                                ("file", "方案-旧.docx")]
    recent = search.find_files("方案", runner=runner, roots=[str(tmp_path)], modified_within_days=7, now=now)
    assert [c.name for c in recent] == ["方案项目", "方案-新.docx"]


def test_open_with_an_app_uses_open_dash_a_only_for_safe_targets(tmp_path, monkeypatch):
    project, script, app = tmp_path / "项目", tmp_path / "x.sh", tmp_path / "Code.app"
    project.mkdir()
    app.mkdir()
    script.write_text("x")
    monkeypatch.setattr(opener, "app_roots", lambda: (str(tmp_path),))
    runner = FakeRunner()
    assert opener.open_path(str(project), app=str(app), runner=runner) == "open"
    assert runner.calls[-1] == [OPEN, "-a", str(app), str(project)]
    assert opener.open_path(str(script), app=str(app), runner=runner) == "reveal"  # never run through an app
    assert runner.calls[-1] == [OPEN, "-R", str(script)]
    for bad in (str(project), str(tmp_path / "missing.app"), "relative.app"):
        with pytest.raises(ValueError):
            opener.open_path(str(project), app=bad, runner=runner)
    outside = tmp_path / "other" / "Evil.app"
    outside.mkdir(parents=True)
    monkeypatch.setattr(opener, "app_roots", lambda: ("/Applications",))
    with pytest.raises(ValueError):
        opener.open_path(str(project), app=str(outside), runner=runner)
