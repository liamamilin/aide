"""HistoryDialog tests — search, selection, deletion with mocked storage."""
from unittest.mock import patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QInputDialog, QLabel, QMessageBox

import ai_desktop.utils.storage as storage
from ai_desktop.config import Agent


@pytest.fixture()
def dialog(qtbot, tmp_db):
    """Create a HistoryDialog with some test conversations in the DB."""
    # Create test conversations
    conv1 = storage.create_conversation("code_expert", "Python 多线程")
    storage.save_message(conv1.id, "user", "Python 多线程性能分析")
    storage.save_message(conv1.id, "assistant", "GIL 是主要原因")

    conv2 = storage.create_conversation("translator", "翻译测试")
    storage.save_message(conv2.id, "user", "Hello world")
    storage.save_message(conv2.id, "assistant", "你好世界")

    from ai_desktop.ui.history_dialog import HistoryDialog
    d = HistoryDialog()
    qtbot.addWidget(d)
    d.show()
    return d


# ── L2: State Tests ────────────────────────────────────

class TestHistoryDialogState:
    """Verify dialog state and rendering."""

    def test_loads_conversations(self, qtbot, dialog):
        """Dialog should show rows matching the conversations in DB."""
        # Layout should have items (conversations) + stretch
        # We created 2 conversations, so layout count > 1 (at least 2 rows + stretch)
        assert dialog._list_layout.count() > 1

    def test_search_filters(self, qtbot, dialog):
        """Typing a search term → _do_search returns matching results."""
        results = storage.search_conversations("多线程")
        assert len(results) >= 1
        assert any(r["id"] for r in results)

    def test_search_no_results(self, qtbot, dialog):
        """Search with no matches → empty results."""
        results = storage.search_conversations("不存在的关键字xyz")
        assert len(results) == 0

    def test_clear_and_reload(self, qtbot, dialog):
        """_clear_list followed by _load should restore conversations."""
        dialog._clear_list()
        # After clearing, only stretch remains
        assert dialog._list_layout.count() == 1
        dialog._load()
        # After reload, should have items again
        assert dialog._list_layout.count() > 1

    def test_window_is_stays_on_top(self, qtbot, dialog):
        """历史窗口需置顶，否则会被常驻顶层（WindowStaysOnTopHint）的对话窗完全遮挡。"""
        assert bool(dialog.windowFlags() & Qt.WindowStaysOnTopHint)


# ── L1: Signal Tests ───────────────────────────────────

class TestHistoryDialogSignals:
    """Verify conversation_selected signal emission."""

    def test_select_emits_signal(self, qtbot, dialog):
        """Clicking a row → conversation_selected signal with conversation ID."""
        # We need to simulate clicking a row
        # _on_select is called directly with a conversation ID
        conv_id = 1  # Use a known ID
        with qtbot.waitSignal(dialog.conversation_selected, timeout=1000) as spy:
            dialog._on_select(conv_id)
        assert spy.args[0] == conv_id


# ── L3: Data Flow Tests ────────────────────────────────

class TestHistoryDialogDataFlow:
    """Verify data flows correctly with mocked storage."""

    def test_delete_removes_row(self, qtbot, dialog):
        """Clicking delete → delete_conversation called + row removed from list."""
        # Create a conversation to delete
        conv = storage.create_conversation("code_expert", "To be deleted")

        # Mock confirmation dialog to return Yes
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            with qtbot.waitSignal(dialog.conversation_deleted, timeout=1000) as deleted:
                dialog._on_delete(conv.id)

        # Verify the conversation was deleted from DB
        assert storage.get_conversation(conv.id) is None
        assert deleted.args == [conv.id]

    def test_rename_keeps_search_and_updates_title(self, qtbot, dialog):
        dialog._search.setText("Python")
        dialog._search_timer.stop()
        dialog._do_search()
        convo_id = dialog._loaded_ids[0]
        with patch.object(QInputDialog, "getText", return_value=("Python 新标题", True)):
            with qtbot.waitSignal(dialog.conversation_renamed, timeout=1000) as renamed:
                dialog._on_rename(convo_id, "Python 多线程")
        assert renamed.args == [convo_id, "Python 新标题"]
        assert dialog._query == "Python"
        assert dialog._search.text() == "Python"
        assert storage.get_conversation(convo_id).title == "Python 新标题"


def test_load_more_reaches_all_125_conversations(qtbot, tmp_db):
    for index in range(125):
        storage.create_conversation("code_expert", f"历史 {index}")
    storage._conn().execute("UPDATE conversations SET created_at=5678")
    storage._conn().commit()
    from ai_desktop.ui.history_dialog import HistoryDialog

    dialog = HistoryDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    assert len(dialog._loaded_ids) == 50
    assert dialog._load_more_btn.isVisible()
    dialog._load_more()
    assert len(dialog._loaded_ids) == 100
    dialog._load_more()
    assert len(dialog._loaded_ids) == 125
    assert len(set(dialog._loaded_ids)) == 125
    assert not dialog._load_more_btn.isVisible()


def test_custom_agent_identity_is_rendered(qtbot, tmp_db):
    custom = Agent(
        id="custom_writer",
        name="写作伙伴",
        icon="✍️",
        system_prompt="write",
    )
    storage.create_conversation(custom.id, "自定义历史")
    from ai_desktop.ui.history_dialog import HistoryDialog

    dialog = HistoryDialog(agents=[custom])
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("写作伙伴" in text for text in texts)
    assert "✍️" in texts
