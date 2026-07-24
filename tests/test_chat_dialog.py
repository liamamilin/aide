"""ChatDialog tests — signals, state transitions, stream lifecycle."""
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import Qt

from ai_desktop.config import Agent

# ── Helpers ──────────────────────────────────────────────

AGENTS = [
    Agent(id="code_expert", name="代码专家", icon="💻", system_prompt="You are a code expert."),
    Agent(id="translator", name="翻译", icon="🌐", system_prompt="You are a translator."),
    Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="You are a helpful assistant."),
]
ACTIVE = AGENTS[2]  # general_assistant
MODELS = ["qwen3:14b", "llama3:8b"]


@pytest.fixture()
def dialog(qtbot):
    """Create a ChatDialog with mocked pin_to_all_spaces."""
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        from ai_desktop.ui.chat_dialog import ChatDialog
        d = ChatDialog(agents=AGENTS, active_agent=ACTIVE, models=MODELS, active_model="qwen3:14b")
        qtbot.addWidget(d)
        d.show()
        return d


# ── L1: Signal Connection Tests ────────────────────────

class TestChatDialogSignals:
    """Verify signals are correctly connected and emitted."""

    def test_send_emits_message_sent(self, qtbot, dialog):
        dialog._input.setPlainText("Hello world")
        with qtbot.waitSignal(dialog.message_sent, timeout=1000) as spy:
            dialog._on_send()
        assert spy.args == ["Hello world"]

    def test_send_clears_input(self, qtbot, dialog):
        dialog._input.setPlainText("Hello")
        dialog._on_send()
        assert dialog._input.toPlainText() == ""

    def test_send_empty_does_not_emit(self, qtbot, dialog):
        dialog._input.setPlainText("")
        with qtbot.assertNotEmitted(dialog.message_sent, wait=200):
            dialog._on_send()

    def test_agent_combo_emits_agent_changed(self, qtbot, dialog):
        """Switching agent combo → agent_changed signal with correct Agent."""
        with qtbot.waitSignal(dialog.agent_changed, timeout=1000) as spy:
            dialog._agent_combo.setCurrentIndex(0)  # code_expert
        assert spy.args[0].id == "code_expert"

    def test_model_combo_emits_model_changed(self, qtbot, dialog):
        """Switching model combo → model_changed signal with model name."""
        with qtbot.waitSignal(dialog.model_changed, timeout=1000) as spy:
            dialog._model_combo.setCurrentText("llama3:8b")
        assert spy.args[0] == "llama3:8b"

    def test_new_convo_button_emits_signal(self, qtbot, dialog):
        """Clicking the new conversation button → new_convo_requested signal."""
        # Find the "＋ 新对话" button in the title bar
        new_btn = None
        for child in dialog.findChildren(object):
            if hasattr(child, 'text') and callable(child.text) and "新对话" in child.text():
                new_btn = child
                break
        assert new_btn is not None, "Could not find new conversation button"
        with qtbot.waitSignal(dialog.new_convo_requested, timeout=1000):
            qtbot.mouseClick(new_btn, Qt.LeftButton)

    def test_history_button_emits_signal(self, qtbot, dialog):
        """Clicking the history button → history_requested signal."""
        hist_btn = None
        for child in dialog.findChildren(object):
            if hasattr(child, 'text') and callable(child.text) and "历史" in child.text():
                hist_btn = child
                break
        assert hist_btn is not None, "Could not find history button"
        with qtbot.waitSignal(dialog.history_requested, timeout=1000):
            qtbot.mouseClick(hist_btn, Qt.LeftButton)

    def test_export_button_emits_signal(self, qtbot, dialog):
        """Clicking the export button → export_requested signal."""
        with qtbot.waitSignal(dialog.export_requested, timeout=1000):
            qtbot.mouseClick(dialog._export_btn, Qt.LeftButton)

    def test_stop_button_emits_signal(self, qtbot, dialog):
        """When thinking=True, clicking stop → stop_requested signal."""
        dialog.set_thinking(True)
        assert dialog._send_btn.text() == "⏹"
        with qtbot.waitSignal(dialog.stop_requested, timeout=1000):
            qtbot.mouseClick(dialog._send_btn, Qt.LeftButton)


# ── L2: State Transition Tests ──────────────────────────

class TestChatDialogState:
    """Verify widget state changes correctly."""

    def test_add_user_message_creates_bubble(self, qtbot, dialog):
        """add_user_message() → one more widget in _msg_layout."""
        initial_count = dialog._msg_layout.count()
        dialog.add_user_message("Hello")
        # Layout should have one more item (the bubble widget)
        assert dialog._msg_layout.count() == initial_count + 1

    def test_add_assistant_message_renders_markdown(self, qtbot, dialog):
        """add_assistant_message() → bubble contains HTML-rendered content."""
        dialog.add_assistant_message("**bold text**")
        # The last non-stretch widget should contain HTML
        # Find the label in the last bubble
        labels = dialog._msg_container.findChildren(object)
        html_found = any(
            hasattr(label, 'text') and callable(label.text) and "<b>bold text</b>" in label.text()
            for label in labels
        )
        assert html_found, "Assistant message should contain rendered HTML"

    def test_stream_lifecycle(self, qtbot, dialog):
        """begin → append_chunk → finalize → bubble shows final text."""
        dialog.begin_assistant_stream()
        dialog.append_stream_chunk("Hello ")
        dialog.append_stream_chunk("world")
        dialog.finalize_assistant_stream("Hello world", ok=True)
        # After finalize, the stream bubble should contain the final text
        labels = dialog._msg_container.findChildren(object)
        text_found = any(
            hasattr(label, 'text') and callable(label.text) and "Hello world" in label.text()
            for label in labels
        )
        assert text_found, "Finalized stream should contain the full text"

    def test_thinking_folded(self, qtbot, dialog):
        """Thinking content should produce <details> tag after finalize."""
        dialog.begin_assistant_stream()
        dialog.append_thinking_chunk("Let me think...")
        dialog.append_stream_chunk("The answer is 42")
        dialog.finalize_assistant_stream("The answer is 42", ok=True)
        labels = dialog._msg_container.findChildren(object)
        details_found = any(
            hasattr(label, 'text') and callable(label.text) and "<details" in label.text()
            for label in labels
        )
        assert details_found, "Thinking content should be wrapped in <details>"

    def test_thinking_html_is_escaped(self, qtbot, dialog):
        """Thinking content should be escaped before RichText rendering."""
        dialog.begin_assistant_stream()
        dialog.append_thinking_chunk("<script>alert(1)</script>")
        dialog.append_stream_chunk("safe")
        dialog.finalize_assistant_stream("safe", ok=True)
        labels = dialog._msg_container.findChildren(object)
        escaped_found = any(
            hasattr(label, 'text') and callable(label.text) and "&lt;script&gt;alert(1)&lt;/script&gt;" in label.text()
            for label in labels
        )
        assert escaped_found

    def test_set_thinking_toggles_send_button(self, qtbot, dialog):
        """set_thinking(True) → button shows ⏹; set_thinking(False) → shows 发送."""
        dialog.set_thinking(True)
        assert dialog._send_btn.text() == "⏹"
        assert not dialog._input.isEnabled()

        dialog.set_thinking(False)
        assert dialog._send_btn.text() == "发送"
        assert dialog._input.isEnabled()

    def test_clear_messages(self, qtbot, dialog):
        """Add messages → clear_messages() → layout only has stretch."""
        dialog.add_user_message("Hello")
        dialog.add_assistant_message("Hi there")
        assert dialog._msg_layout.count() > 1  # stretch + 2 bubbles
        dialog.clear_messages()
        # Only the stretch item should remain
        assert dialog._msg_layout.count() == 1

    def test_set_active_agent(self, qtbot, dialog):
        """set_active_agent() → combo updates + title updates."""
        new_agent = AGENTS[0]  # code_expert
        dialog.set_active_agent(new_agent)
        assert dialog._agent_combo.currentData() == new_agent.id
        assert dialog._title_name.text() == new_agent.name
        assert dialog._title_icon.text() == new_agent.icon

    def test_refresh_agents(self, qtbot, dialog):
        """refresh_agents() → combo items match new agent list."""
        # Must include the currently active agent (general_assistant) or refresh fails
        new_agents = [
            Agent(id="code_expert", name="代码专家", icon="💻", system_prompt="..."),
            Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="..."),
            Agent(id="custom_1", name="设计师", icon="🎨", system_prompt="..."),
        ]
        dialog.refresh_agents(new_agents)
        assert dialog._agent_combo.count() == 3

    def test_set_input_text(self, qtbot, dialog):
        """set_input_text() → input field has text and is selected."""
        dialog.set_input_text("test query")
        assert dialog._input.toPlainText() == "test query"


# ── L3: Data Flow Tests ────────────────────────────────

class TestChatDialogDataFlow:
    """Verify data flows correctly through the widget with mocked dependencies."""

    def test_clipboard_copy_mocked(self, qtbot, dialog):
        """Copying to clipboard should work with mocked QApplication.clipboard()."""
        from ai_desktop.ui.chat_dialog import ChatDialog
        mock_clipboard = MagicMock()
        with patch("ai_desktop.ui.chat_dialog.QApplication.clipboard", return_value=mock_clipboard):
            ChatDialog._copy_to_clipboard("test text")
            mock_clipboard.setText.assert_called_once_with("test text")

    def test_ollama_ping_mocked(self, qtbot, dialog):
        """_OllamaPingWorker should emit result via mocked requests."""
        from ai_desktop.ui.chat_dialog import _OllamaPingWorker
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            worker = _OllamaPingWorker()
            with qtbot.waitSignal(worker.result, timeout=3000) as spy:
                worker.run()
            assert spy.args == [True]


# ── L4: refresh_models Tests ─────────────────────────────

class TestChatDialogRefreshModels:
    """Verify refresh_models() updates the combo and preserves selection."""

    def test_refresh_preserves_selection(self, qtbot, dialog):
        """refresh_models() keeps current selection if it's still in the new list."""
        # Current selection is "qwen3:14b"
        new_models = ["qwen3:14b", "llama3:8b", "qwen3.6:27b-mlx"]
        dialog.refresh_models(new_models)
        assert dialog._model_combo.count() == 3
        assert dialog._model_combo.currentText() == "qwen3:14b"
        assert dialog._active_model == "qwen3:14b"

    def test_refresh_fallback_to_first_when_missing(self, qtbot, dialog):
        """When current selection disappears, fall back to the first item and emit."""
        with qtbot.waitSignal(dialog.model_changed, timeout=1000) as spy:
            dialog.refresh_models(["qwen3.6:27b-mlx", "qwen3.5:9b-mlx"])
        assert dialog._model_combo.currentText() == "qwen3.6:27b-mlx"
        assert dialog._active_model == "qwen3.6:27b-mlx"
        assert spy.args == ["qwen3.6:27b-mlx"]

    def test_refresh_empty_skips(self, qtbot, dialog):
        """refresh_models([]) leaves the combo unchanged."""
        before = dialog._model_combo.count()
        before_text = dialog._model_combo.currentText()
        with qtbot.assertNotEmitted(dialog.model_changed, wait=200):
            dialog.refresh_models([])
        assert dialog._model_combo.count() == before
        assert dialog._model_combo.currentText() == before_text

    def test_refresh_replaces_placeholder_on_empty_init(self, qtbot):
        """A dialog constructed without models shows placeholder, refresh replaces it."""
        with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
            from ai_desktop.ui.chat_dialog import ChatDialog
            d = ChatDialog(agents=AGENTS, active_agent=ACTIVE, models=None, active_model="qwen3.5:9b-mlx")
            qtbot.addWidget(d)
            # Placeholder present + disabled
            assert d._model_combo.count() == 1
            assert d._model_combo.currentText() == "加载中…"
            assert not d._model_combo.isEnabled()
            # Refresh enables and populates
            d.refresh_models(["qwen3.5:9b-mlx", "glm-5.1:cloud"])
            assert d._model_combo.isEnabled()
            assert d._model_combo.count() == 2
            assert d._model_combo.currentText() == "qwen3.5:9b-mlx"

    def test_refresh_preserves_active_model_over_placeholder(self, qtbot):
        """构造时无 models（占位），refresh_models 应保留 active_model 而非退回 models[0]。"""
        with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
            from ai_desktop.ui.chat_dialog import ChatDialog
            d = ChatDialog(agents=AGENTS, active_agent=ACTIVE, models=None,
                           active_model="glm-5.1:cloud")
            qtbot.addWidget(d)
            # 占位状态
            assert d._model_combo.currentText() == "加载中…"
            # 不应 emit model_changed（active_model 在新列表里，且不是第一项）
            with qtbot.assertNotEmitted(d.model_changed, wait=200):
                d.refresh_models(["qwen3.6:27b-mlx", "qwen3.5:9b-mlx", "glm-5.1:cloud"])
            assert d._model_combo.currentText() == "glm-5.1:cloud"
            assert d._active_model == "glm-5.1:cloud"
