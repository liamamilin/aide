"""ChatDialog tests — signals, state transitions, stream lifecycle."""
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor

from ai_desktop.config import Agent
from ai_desktop.llm.service_checks import ImageCapability, ServiceState
from ai_desktop.services.action_service import BUILTIN_ACTIONS

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
        assert spy.args[0] == "Hello world"
        assert spy.args[1] == []

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
        """Choosing history from the compact menu → history_requested signal."""
        with qtbot.waitSignal(dialog.history_requested, timeout=1000):
            dialog._history_action.trigger()

    def test_export_button_emits_signal(self, qtbot, dialog):
        """Choosing export from the compact menu → export_requested signal."""
        with qtbot.waitSignal(dialog.export_requested, timeout=1000):
            dialog._export_action.trigger()

    def test_manage_agents_menu_emits_signal(self, qtbot, dialog):
        with qtbot.waitSignal(dialog.manage_agents_requested, timeout=1000):
            dialog._manage_agents_action.trigger()

    def test_stop_button_emits_signal(self, qtbot, dialog):
        """When thinking=True, clicking stop → stop_requested signal."""
        dialog.set_thinking(True)
        assert dialog._send_btn.text() == "⏹"
        with qtbot.waitSignal(dialog.stop_requested, timeout=1000):
            qtbot.mouseClick(dialog._send_btn, Qt.LeftButton)

    def test_action_signal_includes_current_conversation_mode(self, qtbot):
        from ai_desktop.ui.chat_dialog import ChatDialog

        dialog = ChatDialog(
            AGENTS,
            ACTIVE,
            MODELS,
            MODELS[0],
            actions=list(BUILTIN_ACTIONS),
        )
        qtbot.addWidget(dialog)
        dialog.set_action_context(True, "current")
        dialog.show_actions("material")
        with qtbot.waitSignal(dialog.action_requested, timeout=1000) as signal:
            dialog._action_panel._trigger(0)
        assert signal.args == ["translate", "material", "current"]

    def test_disabling_actions_hides_button_and_panel(self, qtbot):
        from ai_desktop.ui.chat_dialog import ChatDialog

        dialog = ChatDialog(
            AGENTS,
            ACTIVE,
            MODELS,
            MODELS[0],
            actions=list(BUILTIN_ACTIONS),
        )
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.show_actions("material")
        assert dialog._action_panel.isVisible()
        dialog.set_actions_enabled(False)
        assert not dialog._action_btn.isVisible()
        assert not dialog._action_panel.isVisible()
        dialog.show_actions("other")
        assert not dialog._action_panel.isVisible()


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

    def test_message_bubble_forwards_read_selection(self, qtbot, dialog):
        dialog.add_assistant_message("hello world")
        labels = dialog._msg_container.findChildren(object)
        label = next(item for item in labels if hasattr(item, "read_selection_requested"))
        with qtbot.waitSignal(dialog.read_selection_requested, timeout=1000) as signal:
            label.read_selection_requested.emit("hello")
        assert signal.args == ["hello"]

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

    @pytest.mark.parametrize(("capability", "label"), [
        (ImageCapability.SUPPORTED, "支持图片"),
        (ImageCapability.UNSUPPORTED, "不支持图片"),
        (ImageCapability.UNKNOWN, "图片待确认"),
    ])
    def test_image_capability_badge(self, dialog, capability, label):
        dialog.set_image_capability(capability)
        assert dialog._model_capability_badge.text() == label
        assert dialog._model_capability_badge.toolTip() in dialog._attach_btn.toolTip()

    def test_model_profile_is_rendered_as_status_text(self, dialog):
        dialog.set_model_profile_summary("", "使用全局参数")
        assert dialog._model_profile_badge.text() == "全局配置"
        dialog.set_model_profile_summary("翻译专用", "使用专属参数")
        assert dialog._model_profile_badge.text() == "专属配置"
        assert "翻译专用" in dialog._model_profile_badge.toolTip()

    def test_clear_messages(self, qtbot, dialog):
        """Add messages → clear_messages() → restore the onboarding state."""
        dialog.add_user_message("Hello")
        dialog.add_assistant_message("Hi there")
        assert dialog._msg_layout.count() > 1  # stretch + 2 bubbles
        dialog.clear_messages()
        # Empty-state widget and stretch remain.
        assert dialog._msg_layout.count() == 2
        assert not dialog._empty_state.isHidden()

    def test_set_active_agent(self, qtbot, dialog):
        """set_active_agent() → combo and compact header context update."""
        new_agent = AGENTS[0]  # code_expert
        dialog.set_active_agent(new_agent)
        assert dialog._agent_combo.currentData() == new_agent.id
        assert dialog._title_name.text() == "AI 桌面助手"
        assert dialog._agent_combo.toolTip() == new_agent.name

    def test_first_message_hides_empty_state(self, dialog):
        assert not dialog._empty_state.isHidden()
        dialog.add_user_message("开始")
        assert dialog._empty_state.isHidden()

    def test_toolbar_fits_at_minimum_width(self, qtbot, dialog):
        dialog.resize(dialog.minimumWidth(), dialog.height())
        qtbot.wait(10)
        right_edge = dialog._more_btn.mapTo(dialog, dialog._more_btn.rect().bottomRight()).x()
        assert right_edge <= dialog.width()

    def test_long_answer_can_return_to_latest_after_reading_earlier_text(self, qtbot, dialog):
        dialog.resize(400, 460)
        dialog.add_assistant_message("Long answer. " * 250)
        qtbot.waitUntil(lambda: dialog._scroll.verticalScrollBar().maximum() > 100)
        bar = dialog._scroll.verticalScrollBar()
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
        bar.setValue(0)
        dialog._on_user_scroll_position(0)
        qtbot.waitUntil(lambda: dialog._latest_btn.isVisible())
        assert dialog._user_scrolled_up
        assert dialog._latest_btn.accessibleName() == "回到最新消息"
        assert dialog._latest_btn.geometry().right() < dialog._scroll.viewport().width()
        dialog._latest_btn.click()
        qtbot.waitUntil(lambda: bar.value() == bar.maximum())
        assert not dialog._latest_btn.isVisible()

    def test_compact_header_and_composer_have_clear_status(self, dialog):
        dialog.set_service_status(ServiceState.ONLINE)
        assert dialog._ollama_label.text() == "已连接"
        assert dialog._input.placeholderText() == "输入消息…"
        assert dialog._input.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    def test_completed_answer_actions_are_visible_and_copy_confirms(self, qtbot, dialog):
        from PyQt5.QtWidgets import QApplication

        dialog.add_assistant_message("A useful answer")
        qtbot.wait(10)
        copy = dialog.findChildren(object, "copy_btn_assistant")[-1]
        assert copy.isVisible()
        assert copy.text() == "复制"
        copy.click()
        assert QApplication.clipboard().text() == "A useful answer"
        assert copy.text() == "已复制"

    def test_long_context_names_keep_tooltips_and_accessible_controls(self, dialog):
        long_agent = Agent(
            id="long_agent",
            name="这是一个用于验证窄窗口显示的超长 Agent 名称",
            icon="🦉",
            system_prompt="...",
        )
        dialog.refresh_agents([long_agent, ACTIVE])
        dialog.set_active_agent(long_agent)
        long_model = "qwen3.5:9b-mlx-very-long-local-profile"
        dialog.refresh_models([long_model])

        assert dialog._agent_combo.toolTip() == long_agent.name
        assert long_model in dialog._model_combo.toolTip()
        assert dialog._input.accessibleName() == "消息输入框"
        assert dialog._send_btn.accessibleName() == "发送消息"
        assert dialog._new_convo_btn.accessibleName() == "开始新对话"
        assert dialog._hide_btn.accessibleName() == "隐藏对话窗口"
        assert dialog._agent_combo.accessibleName() == "选择 Agent"
        assert dialog._model_combo.accessibleName() == "选择模型"
        assert dialog._more_btn.accessibleName() == "更多操作"
        assert dialog._action_btn.accessibleName() == "快捷动作"
        assert dialog._attach_btn.accessibleName() == "添加图片"
        assert dialog._model_capability_badge.accessibleName() == "模型图片能力"
        assert dialog._model_profile_badge.accessibleName() == "模型配置"
        assert dialog._ollama_dot.accessibleName() == "服务连接状态"

    def test_multi_image_bubble_wraps_within_minimum_width(self, qtbot, dialog):
        from PyQt5.QtGui import QColor, QPixmap
        from PyQt5.QtWidgets import QGridLayout, QHBoxLayout

        dialog.resize(dialog.minimumWidth(), dialog.height())
        qtbot.wait(10)
        bubble_max = max(280, min(440, int(dialog.width() * 0.78)))
        for count in (1, 2, 3, 4):
            paths = []
            for index in range(count):
                pixmap = QPixmap(300, 200)
                pixmap.fill(QColor("blue"))
                path = f"/tmp/aide-ui3-test-{count}-{index}.png"
                assert pixmap.save(path)
                paths.append(path)
            box = dialog._build_bubble_images(paths)
            box.show()
            qtbot.wait(10)
            layout = box.layout()
            if count == 1:
                assert isinstance(layout, QHBoxLayout)
            else:
                assert isinstance(layout, QGridLayout)
            assert box.sizeHint().width() <= bubble_max
            assert box.minimumSizeHint().width() <= bubble_max
            box.deleteLater()

    def test_preview_image_buttons_expose_accessible_names(self, qtbot, dialog):
        from PyQt5.QtGui import QColor, QPixmap

        pixmap = QPixmap(200, 200)
        pixmap.fill(QColor("red"))
        path = "/tmp/aide-ui3-accessible.png"
        assert pixmap.save(path)
        dialog.attach_image_paths([path])
        ocr_buttons = dialog.findChildren(object, "ocr_image_btn")
        assert ocr_buttons
        assert all(button.accessibleName() for button in ocr_buttons)
        dialog.clear_pending_images()

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

class TestChatDialogRegeneration:
    """Verify regen/version buttons stay inert without controller state."""

    def test_finalized_stream_keeps_regen_hidden_by_default(self, dialog):
        dialog.begin_assistant_stream()
        dialog.append_stream_chunk("answer")
        dialog.finalize_assistant_stream("answer", ok=True)
        regen_buttons = dialog.findChildren(object, "regen_btn_assistant")
        version_buttons = dialog.findChildren(object, "version_btn_assistant")
        assert regen_buttons and all(not button.isVisible() for button in regen_buttons)
        assert version_buttons and all(not button.isVisible() for button in version_buttons)

    def test_regen_button_emits_request(self, qtbot, dialog):
        dialog.add_assistant_message("answer")
        dialog.set_regenerate_state(True, [])
        with qtbot.waitSignal(dialog.regenerate_requested, timeout=1000):
            dialog._stream_regen_btn.click()

    def test_single_version_keeps_version_button_hidden(self, dialog, qtbot):
        dialog.add_assistant_message("only")
        dialog.set_regenerate_state(True, [{"id": 1, "answer": "only", "active": True}])
        qtbot.wait(10)
        assert dialog._stream_regen_btn.isVisible()
        assert not dialog._stream_version_btn.isVisible()

    def test_two_versions_show_count_label(self, dialog, qtbot):
        dialog.add_assistant_message("second")
        dialog.set_regenerate_state(True, [
            {"id": 1, "answer": "first", "active": False},
            {"id": 2, "answer": "second", "active": True},
        ])
        qtbot.wait(10)
        assert dialog._stream_version_btn.isVisible()
        assert dialog._stream_version_btn.text() == "2/2"

    def test_history_assistant_without_state_has_no_regen(self, dialog):
        dialog.add_assistant_message("history answer")
        regen_buttons = dialog.findChildren(object, "regen_btn_assistant")
        version_buttons = dialog.findChildren(object, "version_btn_assistant")
        assert regen_buttons and all(not button.isVisible() for button in regen_buttons)
        assert version_buttons and all(not button.isVisible() for button in version_buttons)

    def test_show_generation_updates_newest_bubble(self, dialog):
        dialog.add_assistant_message("first answer")
        dialog.add_assistant_message("second answer")
        dialog.set_regenerate_state(True, [
            {"id": 1, "answer": "first answer", "active": False},
            {"id": 2, "answer": "second answer", "active": True},
        ])
        assert dialog.show_generation(1, "first answer") is True
        labels = dialog._msg_container.findChildren(object, "message_bubble")
        assert [label._markdown_source for label in labels] == ["first answer", "first answer"]
        assert dialog._stream_version_btn.text() == "1/2"


class TestChatDialogDataFlow:
    """Verify data flows correctly through the widget with mocked dependencies."""

    def test_clipboard_copy_mocked(self, qtbot, dialog):
        """Copying to clipboard should work with mocked QApplication.clipboard()."""
        from ai_desktop.ui.chat_dialog import ChatDialog
        mock_clipboard = MagicMock()
        with patch("ai_desktop.ui.chat_dialog.QApplication.clipboard", return_value=mock_clipboard):
            ChatDialog._copy_to_clipboard("test text")
            mock_clipboard.setText.assert_called_once_with("test text")

    def test_periodic_service_check_emits_without_worker(self, qtbot, dialog):
        """The window timer delegates checks without creating a blocking worker."""
        dialog._ollama_timer.setInterval(1)
        with qtbot.waitSignal(dialog.service_check_requested, timeout=1000):
            dialog._ollama_timer.start()
        dialog.hide()
        assert not dialog._ollama_timer.isActive()

    @pytest.mark.parametrize("state,tooltip", [
        (ServiceState.CHECKING, "正在检测"),
        (ServiceState.ONLINE, "已连接"),
        (ServiceState.EMPTY, "没有可用模型"),
        (ServiceState.OFFLINE, "未连接"),
        (ServiceState.INVALID, "格式错误"),
    ])
    def test_service_states_are_distinct(self, dialog, state, tooltip):
        dialog.set_service_status(state)
        assert tooltip in dialog._ollama_dot.toolTip()


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


# ── L5: 输入历史浏览 Tests ─────────────────────────────

class TestChatDialogInputHistory:
    """上下键浏览输入历史（readline 式：Up 随时进入，多行光标边界规则，编辑不退出）"""

    def _press(self, qtbot, dialog, key):
        qtbot.keyClick(dialog._input, key)

    def _move_cursor_to(self, dialog, block: int):
        cursor = dialog._input.textCursor()
        cursor.movePosition(QTextCursor.Start)
        for _ in range(block):
            cursor.movePosition(QTextCursor.Down)
        dialog._input.setTextCursor(cursor)

    def test_up_from_empty_loads_newest(self, qtbot, dialog):
        dialog.set_input_history(["新消息", "旧消息"])
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "新消息"
        assert dialog._hist_index == 0

    def test_up_with_keypad_modifier_browses(self, qtbot, dialog):
        """macOS 箭头键携带 KeypadModifier，仍应进入浏览"""
        dialog.set_input_history(["新消息", "旧消息"])
        qtbot.keyClick(dialog._input, Qt.Key_Up, Qt.KeypadModifier)
        assert dialog._input.toPlainText() == "新消息"
        assert dialog._hist_index == 0

    def test_up_then_up_goes_older(self, qtbot, dialog):
        dialog.set_input_history(["最新", "中间", "最旧"])
        self._press(qtbot, dialog, Qt.Key_Up)
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "中间"

    def test_up_at_oldest_stays(self, qtbot, dialog):
        dialog.set_input_history(["最新", "最旧"])
        self._press(qtbot, dialog, Qt.Key_Up)
        self._press(qtbot, dialog, Qt.Key_Up)
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "最旧"

    def test_down_restores_empty_and_exits(self, qtbot, dialog):
        dialog.set_input_history(["新", "旧"])
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "新"
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == ""
        assert dialog._hist_index == -1

    def test_down_returns_to_newest_then_draft(self, qtbot, dialog):
        dialog.set_input_history(["最新", "中间", "最旧"])
        self._press(qtbot, dialog, Qt.Key_Up)
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "中间"
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == "最新"
        assert dialog._hist_index == 0
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == ""
        assert dialog._hist_index == -1

    def test_up_with_typed_text_browses_and_restores_draft(self, qtbot, dialog):
        """手动输入的文本也支持 Up 浏览历史，Down 恢复原文"""
        dialog.set_input_history(["历史消息", "更旧的"])
        dialog._input.setPlainText("正在输入")
        cursor = dialog._input.textCursor()
        cursor.movePosition(QTextCursor.End)
        dialog._input.setTextCursor(cursor)
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "历史消息"
        assert dialog._hist_index == 0
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == "正在输入"
        assert dialog._hist_index == -1

    def test_up_with_autofilled_text_browses_and_restores_draft(self, qtbot, dialog):
        """热键自动填入的文字同样支持 Up 浏览历史，Down 恢复原文"""
        dialog.set_input_history(["历史消息", "更旧的"])
        dialog.set_input_text("选中文字")
        assert dialog._input.toPlainText() == "选中文字"
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "历史消息"
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == "选中文字"
        assert dialog._hist_index == -1

    def test_up_without_history_does_nothing(self, qtbot, dialog):
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == ""
        assert dialog._hist_index == -1

    def test_edit_keeps_browsing(self, qtbot, dialog):
        """浏览中手动编辑不退出，仍可继续 Up 切换更旧条目"""
        dialog.set_input_history(["新", "旧"])
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "新"
        assert dialog._hist_index == 0
        dialog._input.setPlainText("改过了")
        assert dialog._hist_index == 0
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "旧"
        assert dialog._hist_index == 1

    def test_multiline_up_moves_cursor_then_browses(self, qtbot, dialog):
        dialog.set_input_history(["第一行\n第二行\n第三行", "上一句"])
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "第一行\n第二行\n第三行"
        # 光标在末尾（第三行）
        assert dialog._input.textCursor().blockNumber() == 2
        # 不在首行 → Up 移动光标
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "第一行\n第二行\n第三行"
        assert dialog._input.textCursor().blockNumber() == 1
        # 到首行后 Up 切上一条
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.textCursor().blockNumber() == 0
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._input.toPlainText() == "上一句"

    def test_multiline_down_moves_cursor_then_browses(self, qtbot, dialog):
        dialog.set_input_history(["最新单行", "第一行\n第二行\n第三行"])
        self._press(qtbot, dialog, Qt.Key_Up)  # 最新单行
        self._press(qtbot, dialog, Qt.Key_Up)  # 多行条目（光标末行）
        assert dialog._input.toPlainText() == "第一行\n第二行\n第三行"
        # 不在末行 → Down 移动光标
        self._move_cursor_to(dialog, 0)
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == "第一行\n第二行\n第三行"
        assert dialog._input.textCursor().blockNumber() == 1
        # 到末行后 Down 切下一条
        self._move_cursor_to(dialog, 2)
        self._press(qtbot, dialog, Qt.Key_Down)
        assert dialog._input.toPlainText() == "最新单行"

    def test_add_input_history_dedup(self, qtbot, dialog):
        dialog.add_input_history("hello")
        dialog.add_input_history("world")
        dialog.add_input_history("hello")
        assert dialog._input_history == ["hello", "world"]

    def test_add_input_history_empty_ignored(self, qtbot, dialog):
        dialog.add_input_history("  ")
        assert dialog._input_history == []

    def test_set_input_history_dedups_and_strips(self, qtbot, dialog):
        dialog.set_input_history(["a", "b", "a", "  ", "c"])
        assert dialog._input_history == ["a", "b", "c"]

    def test_send_records_history_and_exits_browsing(self, qtbot, dialog):
        dialog.set_input_history(["旧消息"])
        self._press(qtbot, dialog, Qt.Key_Up)
        assert dialog._hist_index == 0
        with qtbot.waitSignal(dialog.message_sent, timeout=1000):
            dialog._on_send()
        assert dialog._input_history == ["旧消息"]
        assert dialog._hist_index == -1
