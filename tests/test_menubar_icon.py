"""MenuBarIcon tests — agent menu items and active agent checkmark."""

import pytest

from ai_desktop.config import Agent

AGENTS = [
    Agent(id="code_expert", name="代码专家", icon="💻", system_prompt="..."),
    Agent(id="translator", name="翻译", icon="🌐", system_prompt="..."),
    Agent(id="general_assistant", name="通用助手", icon="🤖", system_prompt="..."),
]
ACTIVE = AGENTS[2]  # general_assistant


@pytest.fixture()
def tray(qtbot):
    """Create a MenuBarIcon with test agents.

    QSystemTrayIcon is not a QWidget, so we can't use qtbot.addWidget().
    Instead, we just create it and ensure cleanup.
    """
    from ai_desktop.ui.menubar_icon import MenuBarIcon
    icon = MenuBarIcon(agents=AGENTS, active_agent=ACTIVE)
    yield icon
    # Cleanup
    icon.hide()
    icon.deleteLater()


# ── L2: State Tests ────────────────────────────────────

class TestMenuBarIconState:
    """Verify menu structure and active agent state."""

    def test_agent_menu_items(self, qtbot, tray):
        """Menu should have one action per agent."""
        menu = tray.contextMenu()
        assert menu is not None
        # Count agent actions (checkable ones)
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        assert len(agent_actions) == len(AGENTS)

    def test_agent_menu_item_names(self, qtbot, tray):
        """Each agent action should show 'icon name' format."""
        menu = tray.contextMenu()
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        for action, agent in zip(agent_actions, AGENTS):
            assert agent.icon in action.text()
            assert agent.name in action.text()

    def test_set_active_agent(self, qtbot, tray):
        """set_active_agent() → corresponding action is checked."""
        new_agent = AGENTS[0]  # code_expert
        tray.set_active_agent(new_agent)
        # Find the action for code_expert
        menu = tray.contextMenu()
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        # The code_expert action should be checked
        for action in agent_actions:
            if "代码专家" in action.text():
                assert action.isChecked()
            else:
                assert not action.isChecked()

    def test_initial_active_agent_checked(self, qtbot, tray):
        """Initially, the active agent's menu item should be checked."""
        menu = tray.contextMenu()
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        # general_assistant should be checked initially
        for action in agent_actions:
            if "通用助手" in action.text():
                assert action.isChecked()
            else:
                assert not action.isChecked()


# ── L1: Signal Tests ───────────────────────────────────

class TestMenuBarIconSignals:
    """Verify signal emission from menu actions."""

    def test_agent_selected_signal(self, qtbot, tray):
        """Clicking an agent action → agent_selected signal with correct Agent."""
        menu = tray.contextMenu()
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        # Find the code_expert action
        code_expert_action = None
        for action in agent_actions:
            if "代码专家" in action.text():
                code_expert_action = action
                break
        assert code_expert_action is not None
        with qtbot.waitSignal(tray.agent_selected, timeout=1000) as spy:
            code_expert_action.trigger()
        assert spy.args[0].id == "code_expert"

    def test_dialog_toggle_signal(self, qtbot, tray):
        """The '打开对话' action → dialog_toggle signal."""
        menu = tray.contextMenu()
        show_action = None
        for action in menu.actions():
            if action.text() == "打开对话":
                show_action = action
                break
        assert show_action is not None
        with qtbot.waitSignal(tray.dialog_toggle, timeout=1000):
            show_action.trigger()

    def test_settings_clicked_signal(self, qtbot, tray):
        """The '设置…' action → settings_clicked signal."""
        menu = tray.contextMenu()
        settings_action = None
        for action in menu.actions():
            if action.text() == "设置…":
                settings_action = action
                break
        assert settings_action is not None
        with qtbot.waitSignal(tray.settings_clicked, timeout=1000):
            settings_action.trigger()

    def test_exit_clicked_signal(self, qtbot, tray):
        """The '退出' action → exit_clicked signal."""
        menu = tray.contextMenu()
        exit_action = None
        for action in menu.actions():
            if action.text() == "退出":
                exit_action = action
                break
        assert exit_action is not None
        with qtbot.waitSignal(tray.exit_clicked, timeout=1000):
            exit_action.trigger()

    def test_refresh_agents_rebuilds_menu(self, qtbot, tray):
        """refresh_agents() → menu items match new agent list."""
        new_agents = [
            Agent(id="code_expert", name="代码专家", icon="💻", system_prompt="..."),
            Agent(id="custom_1", name="设计师", icon="🎨", system_prompt="..."),
        ]
        tray.refresh_agents(new_agents)
        menu = tray.contextMenu()
        agent_actions = [a for a in menu.actions() if a.isCheckable()]
        assert len(agent_actions) == 2
