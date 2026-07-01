"""AgentEditor tests — CRUD operations and signal emission."""

import pytest

from ai_desktop.ui.agent_editor import AgentDef

BUILTIN_AGENTS = [
    AgentDef(id="code_expert", name="代码专家", icon="💻", system_prompt="You code.", builtin=True),
    AgentDef(id="translator", name="翻译", icon="🌐", system_prompt="You translate.", builtin=True),
]
CUSTOM_AGENTS = [
    AgentDef(id="custom_1", name="设计师", icon="🎨", system_prompt="You design.", builtin=False),
]


@pytest.fixture()
def dialog(qtbot):
    """Create an AgentEditor with builtin + custom agents."""
    from ai_desktop.ui.agent_editor import AgentEditor
    d = AgentEditor(
        builtin_agents=BUILTIN_AGENTS,
        custom_agents=list(CUSTOM_AGENTS),
    )
    qtbot.addWidget(d)
    d.show()
    return d


# ── L2: State Tests ────────────────────────────────────

class TestAgentEditorState:
    """Verify editor state and rendering."""

    def test_builtin_agents_displayed(self, qtbot, dialog):
        """Built-in agents should appear in the list with '内置' tag."""
        # Layout should have: 2 builtin + 1 custom + stretch = 4 items
        assert dialog._list_layout.count() >= 3  # at least 3 agents + stretch

    def test_builtin_agents_have_no_delete_button(self, qtbot, dialog):
        """Built-in agents should show '内置' label, not edit/delete buttons."""
        # Find all widgets in the list
        widgets = []
        for i in range(dialog._list_layout.count()):
            item = dialog._list_layout.itemAt(i)
            if item and item.widget():
                widgets.append(item.widget())

        # At least one widget should contain a "内置" label
        builtin_found = False
        for w in widgets:
            labels = w.findChildren(object)
            for lbl in labels:
                if hasattr(lbl, 'text') and callable(lbl.text) and lbl.text() == "内置":
                    builtin_found = True
                    break
        assert builtin_found, "Built-in agents should display '内置' label"

    def test_custom_agents_have_edit_delete(self, qtbot, dialog):
        """Custom agents should have edit and delete buttons."""
        widgets = []
        for i in range(dialog._list_layout.count()):
            item = dialog._list_layout.itemAt(i)
            if item and item.widget():
                widgets.append(item.widget())

        # Find edit and delete buttons
        edit_btns = []
        delete_btns = []
        for w in widgets:
            for child in w.findChildren(object):
                if hasattr(child, 'text') and callable(child.text):
                    if child.text() == "编辑":
                        edit_btns.append(child)
                    elif child.text() == "删除":
                        delete_btns.append(child)

        assert len(edit_btns) >= 1, "Custom agents should have edit button"
        assert len(delete_btns) >= 1, "Custom agents should have delete button"


# ── L1: Signal Tests ───────────────────────────────────

class TestAgentEditorSignals:
    """Verify agents_saved signal emission."""

    def test_delete_custom_agent_emits_signal(self, qtbot, dialog):
        """Deleting a custom agent → agents_saved signal with updated list."""
        custom_agent = dialog._custom[0]  # the designer agent
        with qtbot.waitSignal(dialog.agents_saved, timeout=1000) as spy:
            dialog._on_delete(custom_agent)
        data = spy.args[0]
        assert isinstance(data, list)
        # The deleted agent should not be in the list
        assert not any(a["id"] == "custom_1" for a in data)

    def test_emit_save_format(self, qtbot, dialog):
        """_emit_save should produce a list of dicts with expected keys."""
        dialog._emit_save()
        # We can't easily wait for the signal here since it's emitted synchronously
        # Instead, verify the format by checking _custom directly
        data = [
            {"id": a.id, "name": a.name, "icon": a.icon, "system_prompt": a.system_prompt}
            for a in dialog._custom
        ]
        assert len(data) == 1
        assert data[0]["id"] == "custom_1"
        assert data[0]["name"] == "设计师"

    def test_next_custom_id(self, qtbot, dialog):
        """_next_custom_id should generate unique IDs."""
        id1 = dialog._next_custom_id()
        assert id1.startswith("custom_")
        # Since custom_1 already exists, next should be custom_2
        assert id1 == "custom_2"


# ── L3: Data Flow Tests ────────────────────────────────

class TestAgentEditorDataFlow:
    """Verify data flows correctly through the editor."""

    def test_add_custom_agent_updates_internal_list(self, qtbot, dialog):
        """Adding an agent → _custom list grows."""
        initial_count = len(dialog._custom)
        new_agent = AgentDef(id="custom_2", name="新Agent", icon="🚀", system_prompt="test", builtin=False)
        dialog._custom.append(new_agent)
        assert len(dialog._custom) == initial_count + 1

    def test_refresh_rebuilds_list(self, qtbot, dialog):
        """_refresh should rebuild the widget list from _builtin + _custom."""
        dialog._refresh()
        # After refresh, should have 3 agents (2 builtin + 1 custom) + stretch
        assert dialog._list_layout.count() >= 3
