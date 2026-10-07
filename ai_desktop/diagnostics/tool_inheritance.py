"""Saved tool choices, role rebinding and a fresh process; no model or search calls."""
import json
import subprocess


def resume_probe():
    from PyQt5.QtWidgets import QApplication

    from ai_desktop.diagnostics.harness_acceptance import controller_class, isolated_root
    from ai_desktop.ui.fluent import initialize

    root = isolated_root()
    app = QApplication([])
    initialize()
    ctl = controller_class()()
    ctl._restore_last = False
    ctl._show_dialog()
    grant = ctl._task_authorization
    assert grant is not None and grant.agent_id == ctl._active_agent.id
    assert grant.execution.workspace == str(root/'workspace') and grant.execution.valid()
    assert grant.search.provider == 'exa'
    assert ctl._worker is None and ctl._pending_task is None
    assert ctl._dialog._task_btn.text() == '工具：已启用'
    ctl.stop()
    app.processEvents()
    print(json.dumps({'saved_settings_loaded': True, 'pending_task_restored': False,
                      'model_or_tools_executed': False}))


def acceptance(ctl, workspace):
    from ai_desktop.diagnostics.harness_acceptance import child_command
    from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot
    from ai_desktop.services.task_admission import TaskAuthorization
    from ai_desktop.services.tool_preferences import ToolPreferences
    from ai_desktop.services.web_search import SearchSettings
    from ai_desktop.utils import storage

    original = TaskAuthorization(ExecutionSnapshot.create(workspace, BashPolicy.CONFIRM_ALL),
                                 SearchSettings(provider='exa'), agent_id=ctl._active_agent.id)
    ctl._save_tool_settings(original)
    ctl._on_custom_agents_saved([{'id': 'inheritance-fixture', 'name': '继承验收',
                                 'system_prompt': '本机临时验收角色。', 'icon': '📚'}])
    roles = []
    for agent in ctl._all_agents:
        ctl._new_conversation()
        ctl._dialog._agent_combo.setCurrentIndex(ctl._dialog._agent_combo.findData(agent.id))
        grant = ctl._task_authorization
        assert grant is not original and grant.agent_id == agent.id
        assert grant.execution == original.execution and grant.search.provider == 'exa'
        assert ctl._dialog._task_btn.text() == '工具：已启用'
        roles.append(agent.id)
    conversation = storage.create_conversation('translator', '继承验收历史')
    ctl._on_conversation_selected(conversation.id)
    assert ctl._task_authorization.agent_id == 'translator'
    assert ctl._worker is None and ctl._pending_task is None
    result = subprocess.run(child_command()+['--tool-inheritance-resume'],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, 'Saved-settings restart probe failed: '+result.stderr[-300:]
    restarted = json.loads(result.stdout.strip().splitlines()[-1])
    assert restarted['saved_settings_loaded'] and not restarted['pending_task_restored']
    ctl._save_tool_settings(None)
    ctl._new_conversation()
    ctl._on_tray_agent(ctl._all_agents[0])
    assert ctl._task_authorization is None and ToolPreferences.load() is None
    return {'roles_checked': roles, 'new_conversation_inherits': True,
            'agent_switch_inherits': True, 'history_uses_current_settings': True,
            'fresh_process_loads_settings': True, 'disable_applies_to_future_scopes': True,
            'pending_task_restored': False, 'model_or_tools_executed': False,
            'credential_access': False, 'user_data_isolated': True}
