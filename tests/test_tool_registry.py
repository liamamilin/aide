import json

import pytest

from ai_desktop import config
from ai_desktop.llm.ollama_protocol import ToolCall
from ai_desktop.llm.run_types import ToolOutput, ToolSpec, counts_as_search, validate_tool_arguments
from ai_desktop.llm.tool_registry import (
    BASH,
    WEB_SEARCH,
    ToolBudget,
    ToolDefinition,
    ToolRisk,
    get_definition,
    register,
    registered_names,
)


def _call(name, **args):
    return ToolCall("local", name, json.dumps(args))


def test_registered_tools_match_chat_grantable_names():
    # Registering a tool makes it nameable; chat grants stay an explicit allow-list.
    assert registered_names() == config.CHAT_TOOL_NAMES
    assert {BASH.name, WEB_SEARCH.name} <= set(config.CHAT_TOOL_NAMES)


def test_wire_schema_is_built_from_the_definition():
    wire = ToolSpec.create("bash", lambda *_: ToolOutput("ok")).wire()
    assert wire == {"type": "function", "function": {
        "name": "bash",
        "description": "Run a command in the configured workspace, subject to application approval policy.",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string", "minLength": 1, "maxLength": 16000},
                                      "timeout": {"type": "integer", "minimum": 1, "maximum": 120}},
                       "required": ["command"], "additionalProperties": False}}}
    search = ToolSpec.create("web_search", lambda *_: ToolOutput("ok")).wire()
    assert search["function"]["parameters"]["required"] == ["query"]


def test_unknown_tool_is_rejected_everywhere():
    with pytest.raises(ValueError):
        ToolSpec.create("rm_rf", lambda *_: ToolOutput("ok"))
    with pytest.raises(ValueError):
        validate_tool_arguments(_call("rm_rf"))
    assert get_definition("rm_rf") is None
    assert get_definition(None) is None


def test_validation_is_delegated_to_the_definition():
    assert validate_tool_arguments(_call("bash", command="pwd")) == {"command": "pwd"}
    with pytest.raises(ValueError):
        validate_tool_arguments(_call("bash", command="pwd", extra=1))
    with pytest.raises(ValueError):
        validate_tool_arguments(_call("web_search", query=" "))


def test_only_search_counts_against_the_search_budget():
    assert counts_as_search("web_search")
    assert not counts_as_search("bash")
    assert not counts_as_search("unknown")
    assert WEB_SEARCH.budget is ToolBudget.SEARCH and BASH.budget is None


def test_risk_belongs_to_the_tool():
    assert BASH.risk is ToolRisk.DESTRUCTIVE
    assert WEB_SEARCH.risk is ToolRisk.READ


def test_duplicate_registration_is_rejected():
    duplicate = ToolDefinition("bash", "terminal", ToolRisk.READ, "x", {}, (), lambda args: None)
    with pytest.raises(ValueError):
        register(duplicate)


def test_desktop_tools_are_registered_with_risk_and_group():
    assert {n: (get_definition(n).group, get_definition(n).risk) for n in ("find_apps", "find_files", "open_path")} == {
        "find_apps": ("desktop", ToolRisk.READ), "find_files": ("desktop", ToolRisk.READ),
        "open_path": ("desktop", ToolRisk.REVERSIBLE)}
    assert validate_tool_arguments(_call("open_path", path="/Applications/Safari.app"))
    for bad in ({"path": "relative"}, {"path": "/a", "x": 1}, {}):
        with pytest.raises(ValueError):
            validate_tool_arguments(_call("open_path", **bad))
    with pytest.raises(ValueError):
        validate_tool_arguments(_call("find_files", query="x" * 201))
