"""Immutable run configuration and tool contracts; no credentials in snapshots."""
from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from ai_desktop import config
from ai_desktop.llm.active_budget import ActiveBudget
from ai_desktop.llm.events import RequestContext
from ai_desktop.llm.ollama_protocol import ToolCall
from ai_desktop.llm.tool_registry import ToolBudget, get_definition
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.services.web_search import SearchSettings


def chat_tools_eligible(agent_id, origin):
    return (origin == 'chat' and isinstance(agent_id, str) and bool(agent_id.strip())
            and agent_id.strip() == agent_id and not any(ord(char) < 32 for char in agent_id))


class RunEventKind(str, Enum):
    STARTED = "started"
    MODEL_STARTED = "model_started"
    MODEL_FINISHED = "model_finished"
    TOOL_STARTED = "tool_started"
    TOOL_UPDATED = "tool_updated"
    TOOL_FINISHED = "tool_finished"
    CONTEXT_REDUCED = "context_reduced"
    THINKING = "thinking"
    CONTENT = "content"
    FINISHED = "finished"


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    conversation_id: int
    step_id: str
    request_id: str
    seq: int
    kind: RunEventKind
    local_call_id: str | None = None
    provider_call_id: str | None = None
    status: str = ""
    tool_name: str = ""
    arguments_json: str = field(default="", repr=False)
    output: ToolOutput | None = field(default=None, repr=False)
    payload_json: str = field(default='', repr=False)


@dataclass(frozen=True)
class RunLimits:
    max_model_rounds: int = 8
    max_tool_calls: int = 16
    max_search_calls: int = 6
    tool_result_bytes: int = 4096
    active_seconds: float | None = 300
    context_reserve: int = 512

    def __post_init__(self):
        for value, high in ((self.max_model_rounds, 99), (self.max_tool_calls, 99), (self.max_search_calls, 99),
                            (self.tool_result_bytes, 4096)):
            if type(value) is not int or not 1 <= value <= high:
                raise ValueError("Invalid run budget")
        if type(self.context_reserve) is not int or self.context_reserve < 0:
            raise ValueError("Invalid context reserve")
        if self.active_seconds is not None and (
            not isinstance(self.active_seconds, (int, float)) or isinstance(self.active_seconds, bool)
            or not math.isfinite(self.active_seconds) or not 0 < self.active_seconds <= 300
        ):
            raise ValueError("Invalid active-time budget")

    @classmethod
    def from_config(cls):
        return cls(max_model_rounds=config.TASK_MAX_MODEL_ROUNDS,
                   max_tool_calls=config.TASK_MAX_TOOL_CALLS,
                   max_search_calls=config.TASK_MAX_SEARCH_CALLS)


@dataclass(frozen=True)
class ToolOutput:
    text: str
    error: bool = False

    def __post_init__(self):
        if not isinstance(self.text, str) or type(self.error) is not bool:
            raise ValueError("Invalid tool output")


@dataclass(frozen=True)
class ToolExecutionContext:
    request: RequestContext
    call: ToolCall
    cancelled: threading.Event = field(repr=False, compare=False)
    deadline: float | None = None
    execution: ExecutionSnapshot | None = None
    budget: ActiveBudget | None = field(default=None, repr=False, compare=False)
    on_state: Callable[[str], None] | None = field(default=None, repr=False, compare=False)

    @property
    def active_deadline(self):
        return self.budget.deadline if self.budget is not None else self.deadline


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema_json: str
    execute: Callable[[dict, ToolExecutionContext], ToolOutput] = field(repr=False, compare=False)

    def __post_init__(self):
        if get_definition(self.name) is None or not callable(self.execute):
            raise ValueError("Unknown tool")
        schema = json.loads(self.schema_json)
        if not isinstance(schema, dict) or schema.get("type") != "function":
            raise ValueError("Invalid tool schema")
        function = schema.get("function")
        if not isinstance(function, dict) or function.get("name") != self.name:
            raise ValueError("Tool schema name mismatch")

    def wire(self):
        return json.loads(self.schema_json)

    @property
    def counts_as_search(self):
        return counts_as_search(self.name)

    @classmethod
    def create(cls, name, execute):
        definition = get_definition(name)
        if definition is None:
            raise ValueError("Unknown tool")
        schema = {"type": "function", "function": {"name": name, "description": definition.description,
                  "parameters": definition.parameters()}}
        return cls(name, json.dumps(schema, ensure_ascii=False), execute)


@dataclass(frozen=True)
class RunContext:
    request: RequestContext
    tools: tuple[ToolSpec, ...] = ()
    limits: RunLimits = field(default_factory=lambda: RunLimits(max_model_rounds=1, active_seconds=None))
    execution: ExecutionSnapshot | None = None
    search_settings: SearchSettings | None = None

    def __post_init__(self):
        if self.execution is not None and not isinstance(self.execution, ExecutionSnapshot):
            raise ValueError("Invalid execution snapshot")
        if self.search_settings is not None and not isinstance(self.search_settings, SearchSettings):
            raise ValueError("Invalid search snapshot")
        if not isinstance(self.tools, tuple) or len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Invalid tool registry")
        if self.request.origin not in {"chat", "action"}:
            raise ValueError("Unknown run origin")
        if self.tools and not chat_tools_eligible(self.request.agent_id, self.request.origin):
            raise ValueError("This origin or agent cannot use tools")
        if not self.tools and self.limits.max_model_rounds != 1:
            raise ValueError("Zero-tool runs are single-round")
        if self.tools and self.limits.active_seconds is None:
            raise ValueError("Tool runs need an active-time budget")

    @classmethod
    def create(cls, request: RequestContext, *, tools: tuple[ToolSpec, ...] = (),
               tools_admitted: bool = False, limits: RunLimits | None = None,
               execution: ExecutionSnapshot | None = None, search_settings: SearchSettings | None = None):
        if type(tools_admitted) is not bool:
            raise ValueError("Admission must be explicit")
        # Action origin is always tool-free, independent of its bound role.
        allowed = tools_admitted and chat_tools_eligible(request.agent_id, request.origin)
        granted = tuple(tool for tool in tools if tool.name in config.CHAT_TOOL_NAMES) if allowed else ()
        return cls(request, granted, (limits or RunLimits()) if granted else
                   RunLimits(max_model_rounds=1, active_seconds=None), execution if granted else None,
                   search_settings if any(tool.counts_as_search for tool in granted) else None)


def counts_as_search(name) -> bool:
    definition = get_definition(name)
    return definition is not None and definition.budget is ToolBudget.SEARCH


def validate_tool_arguments(call: ToolCall) -> dict:
    args = json.loads(call.arguments_json)
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be an object")
    definition = get_definition(call.name)
    if definition is None:
        raise ValueError("Unknown tool")
    definition.validate(args)
    return args
