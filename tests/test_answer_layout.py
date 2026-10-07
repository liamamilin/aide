"""Wrapped Markdown must remain readable after a scrolled stream completes."""
import pytest

from ai_desktop.diagnostics.answer_layout import (
    CITATION_TEXT,
    STRUCTURED_TEXT,
    fits,
    restored_answer,
    streamed_fixture,
)
from tests.test_chat_dialog import dialog as dialog


@pytest.mark.parametrize('text', [CITATION_TEXT, STRUCTURED_TEXT])
def test_task_markdown_fits_after_stream_without_manual_resize(qtbot, dialog, text):
    dialog.resize(450, 570)
    label, card = streamed_fixture(dialog, text, lambda predicate: qtbot.waitUntil(predicate, timeout=2000))
    qtbot.waitUntil(lambda: fits(label, card), timeout=2000)
    assert 'source://S1' in label.text()
    for width, height in [(400, 460), (900, 700), (450, 570)]:
        dialog.resize(width, height)
        qtbot.waitUntil(lambda: fits(label, card), timeout=2000)


def test_shorter_reply_releases_wrapped_height(qtbot, dialog):
    label, card = streamed_fixture(dialog, STRUCTURED_TEXT,
                                  lambda predicate: qtbot.waitUntil(predicate, timeout=2000))
    qtbot.waitUntil(lambda: fits(label, card), timeout=2000)
    before = label.height()
    label.setText('简短回答。')
    qtbot.waitUntil(lambda: fits(label, card) and label.height() < before, timeout=2000)


def test_history_layout_check_uses_new_label_before_deferred_deletion(qtbot, dialog):
    from PyQt5 import sip
    from PyQt5.QtWidgets import QLabel

    dialog.add_assistant_message(CITATION_TEXT)
    previous = set(dialog.findChildren(QLabel))
    old = next(label for label in previous if getattr(label, '_markdown_source', '') == CITATION_TEXT)
    dialog.clear_messages()
    assert restored_answer(dialog, CITATION_TEXT, previous) is None
    dialog.add_assistant_message(CITATION_TEXT)
    restored = restored_answer(dialog, CITATION_TEXT, previous)
    assert restored is not None and restored not in previous
    qtbot.waitUntil(lambda: sip.isdeleted(old), timeout=2000)
    qtbot.waitUntil(lambda: restored.height() >= restored.heightForWidth(restored.width()), timeout=2000)
    assert not sip.isdeleted(restored)
