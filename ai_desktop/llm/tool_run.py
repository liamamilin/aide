"""Qt-free assembly of a tool-capable run: which tools a request may use, and the prompt that governs them.

Shared by the Qt ``RunWorker`` and the headless core so the admission gating lives in one place.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ai_desktop import config
from ai_desktop.desktop.runner import Runner, run_command
from ai_desktop.desktop.tools import DesktopToolbox
from ai_desktop.llm.events import RequestContext
from ai_desktop.llm.run_types import RunContext, RunLimits, ToolSpec, chat_tools_eligible
from ai_desktop.llm.tool_prompt import fit_tools, tool_rules
from ai_desktop.services.bash_executor import BashExecutor
from ai_desktop.services.command_confirmation import ConfirmationBroker
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.services.web_search import SearchSettings


@dataclass(frozen=True)
class ToolAssembly:
    request: RequestContext
    context: RunContext
    confirmations: ConfirmationBroker | None
    search_executor: object | None = None


def assemble_tool_run(request: RequestContext, system_prompt: str, *, tools_admitted: bool,
                      execution: ExecutionSnapshot | None = None,
                      search_settings: SearchSettings | None = None, native_tools: bool = False,
                      confirmations: ConfirmationBroker | None = None,
                      make_broker: Callable[[], ConfirmationBroker] | None = None,
                      session_context: str = "", desktop_runner: Runner = run_command) -> ToolAssembly:
    """Grant tools only for an admitted chat request; append the tool rules to the system prompt."""
    tools: tuple[ToolSpec, ...] = ()
    allowed = tools_admitted and chat_tools_eligible(request.agent_id, request.origin)
    if allowed and confirmations is None and (execution is not None or native_tools) and make_broker is not None:
        confirmations = make_broker()
    if allowed and execution is not None and confirmations is not None:
        tools = (ToolSpec.create('bash', BashExecutor(confirmations)),)
    search_executor = None
    if allowed and search_settings is not None:
        from ai_desktop.services.search_executor import SearchExecutor
        search_executor = SearchExecutor(search_settings)
        tools += (ToolSpec.create('web_search', search_executor),)
    if allowed and (tools or native_tools) and config.DESKTOP_TOOLS_ENABLED:
        toolbox = DesktopToolbox(runner=desktop_runner, confirmations=confirmations)
        tools += tuple(ToolSpec.create(name, execute) for name, execute in toolbox.executors().items())
        tools = fit_tools(tools, dict(request.options))
    context = RunContext.create(request, tools=tools, tools_admitted=tools_admitted,
                                limits=RunLimits.from_config() if tools_admitted else None,
                                execution=execution, search_settings=search_settings)
    if context.tools:
        rules = tool_rules(tool.name for tool in context.tools)
        if session_context:
            rules += '\n\n' + session_context
        request = replace(request, system_prompt=system_prompt + '\n\n' + rules)
        context = replace(context, request=request)
    return ToolAssembly(request, context, confirmations, search_executor)
