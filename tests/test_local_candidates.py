"""Local app/file candidates in the chat input: explicit pick, never a model call."""
from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest

from ai_desktop import config
from ai_desktop.desktop.search import Candidate
from ai_desktop.ui.candidate_panel import CandidateLookup
from tests.test_chat_dialog import ACTIVE, AGENTS, MODELS

NOTES = Candidate("app", "Notes", "/System/Applications/Notes.app")
DOC = Candidate("file", "Notes plan.docx", "/Users/me/Documents/Notes plan.docx")


@pytest.fixture()
def dialog(qtbot, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_CANDIDATES_ENABLED", True)
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        from ai_desktop.ui.chat_dialog import ChatDialog
        d = ChatDialog(agents=AGENTS, active_agent=ACTIVE, models=MODELS, active_model="qwen3:14b")
        qtbot.addWidget(d)
        d.show()
    d._candidate_lookup._resolve = lambda text: [NOTES, DOC]
    d._candidate_lookup._run_async = False
    d.opened = []
    d._open_path = d.opened.append
    return d


def _type(qtbot, dialog, text):
    dialog._input.setPlainText(text)
    qtbot.waitUntil(lambda: dialog._candidate_panel.isVisible(), timeout=2000)


def test_short_name_shows_candidates_and_sentence_hides_them(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    assert dialog._candidate_panel._list.count() == 2
    dialog._input.setPlainText("请帮我整理一下下载文件夹")
    qtbot.waitUntil(lambda: not dialog._candidate_panel.isVisible(), timeout=2000)


def test_enter_without_selection_still_sends(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    with qtbot.waitSignal(dialog.message_sent, timeout=1000) as spy:
        QTest.keyClick(dialog._input, Qt.Key_Return)
    assert spy.args[0] == "notes" and dialog.opened == []
    assert not dialog._candidate_panel.isVisible()


def test_arrow_then_enter_opens_the_picked_candidate_and_does_not_send(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    QTest.keyClick(dialog._input, Qt.Key_Down)
    QTest.keyClick(dialog._input, Qt.Key_Down)
    with qtbot.assertNotEmitted(dialog.message_sent, wait=100):
        QTest.keyClick(dialog._input, Qt.Key_Return)
    assert dialog.opened == [DOC.path]
    assert dialog._input.toPlainText() == ""


def test_up_from_first_row_clears_the_pick_so_enter_sends(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    QTest.keyClick(dialog._input, Qt.Key_Down)
    QTest.keyClick(dialog._input, Qt.Key_Up)
    assert not dialog._candidate_panel.has_selection()
    with qtbot.waitSignal(dialog.message_sent, timeout=1000):
        QTest.keyClick(dialog._input, Qt.Key_Return)
    assert dialog.opened == []


def test_escape_closes_the_list_but_keeps_the_text(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    QTest.keyClick(dialog._input, Qt.Key_Escape)
    assert not dialog._candidate_panel.isVisible()
    assert dialog._input.toPlainText() == "notes"


def test_click_opens_a_candidate(qtbot, dialog):
    _type(qtbot, dialog, "notes")
    dialog._candidate_panel._on_clicked(dialog._candidate_panel._list.item(0))
    assert dialog.opened == [NOTES.path]


def test_failed_open_keeps_the_input_and_reports(qtbot, dialog):
    def fail(_path):
        raise OSError("nope")
    dialog._open_path = fail
    _type(qtbot, dialog, "notes")
    QTest.keyClick(dialog._input, Qt.Key_Down)
    QTest.keyClick(dialog._input, Qt.Key_Return)
    assert dialog._input.toPlainText() == "notes"
    assert "无法打开" in dialog._candidate_panel._hint.text()


def test_answer_for_old_text_is_dropped(qtbot, dialog):
    dialog._input.setPlainText("notes")
    dialog._on_candidates_resolved(1, "other", [NOTES])
    assert not dialog._candidate_panel.isVisible()


def test_history_arrows_still_work_without_candidates(qtbot, dialog):
    dialog._candidate_lookup._resolve = lambda text: []
    dialog._input_history = ["earlier"]
    QTest.keyClick(dialog._input, Qt.Key_Up)
    assert dialog._input.toPlainText() == "earlier"


def test_lookup_clears_immediately_for_sentences_and_drops_stale_answers(qtbot):
    lookup = CandidateLookup(lambda text: [NOTES], run_async=False)
    seen = []
    lookup.resolved.connect(lambda seq, text, found: seen.append((text, len(found))))
    lookup.request("把文件整理一下")
    assert seen == [("把文件整理一下", 0)]
    lookup.request("notes")
    lookup._on_finished(0, "old", [NOTES])
    assert seen[-1] == ("把文件整理一下", 0)
    qtbot.waitUntil(lambda: ("notes", 1) in seen, timeout=2000)


def test_closed_lookup_never_answers(qtbot):
    lookup = CandidateLookup(lambda text: [NOTES], run_async=False)
    lookup.close()
    seen = []
    lookup.resolved.connect(lambda *args: seen.append(args))
    lookup.request("notes")
    assert seen[0][2] == [] and len(seen) == 1
