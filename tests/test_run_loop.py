"""Production run engine against synthetic model steps and executors."""
import json
import threading
from dataclasses import replace

import pytest

from ai_desktop.llm.chat_client import ChatClient, _payload
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, ResultStatus, StreamEvent
from ai_desktop.llm.ollama_protocol import ModelTurn, ToolCall, TurnAssembler
from ai_desktop.llm.run_loop import ProtocolBlock, RunLoop, bounded_tool_output, initial_blocks
from ai_desktop.llm.run_types import (
    RunContext,
    RunEventKind,
    RunLimits,
    ToolOutput,
    ToolSpec,
    validate_tool_arguments,
)
from ai_desktop.utils.storage import Message


def tool_call(name="bash", args=None, provider_id=None):
    return ToolCall("local-" + name, name, json.dumps(args or {"command": "pwd"}), provider_id)


class FakeModel:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, request, payload, deadline):
        self.requests.append((request, payload, deadline))
        result = self.turns.pop(0)
        if isinstance(result, ModelTurn):
            return ChatResult(request.request_id, ResultStatus.SUCCEEDED, result.content, turn=result,
                              run_id=request.run_id, step_id=request.step_id, conversation_id=request.conversation_id)
        if isinstance(result, dict):
            return ChatResult(request.request_id, **result, run_id=request.run_id, step_id=request.step_id,
                              conversation_id=request.conversation_id)
        return result(request, payload, deadline)


def setup_run(turns, *, tools=True, executor=None, limits=None, messages=None):
    request = ChatClient().create_request(messages or [Message("user", "task")], "SYSTEM",
                                          conversation_id=42, agent_id="general_assistant",
                                          think=False, options={"num_predict": 1024, "num_ctx": 8192})
    cancelled = threading.Event()
    executions = []

    def execute(args, context):
        executions.append((args, context))
        return executor(args, context) if executor else ToolOutput("result")

    specs = tuple(ToolSpec.create(name, execute) for name in ("bash", "web_search"))
    context = RunContext.create(request, tools=specs, tools_admitted=tools, limits=limits)
    model = FakeModel(turns)
    events = []
    loop = RunLoop(context, cancelled, model, on_event=events.append)
    return loop, _payload(request, True), model, executions, events


def assert_single_terminal(loop, events, status):
    assert loop.result.status == status
    assert [e.status for e in events if e.kind == RunEventKind.FINISHED] == [status.value]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert all(e.run_id == loop.context.request.run_id and e.conversation_id == 42 for e in events)


def test_zero_tools_single_turn_payload_and_single_terminal():
    loop, payload, model, executions, events = setup_run([ModelTurn("answer", "thinking", (), "stop")], tools=False)
    assert loop.run(payload).ok
    assert model.requests[0][1] == payload
    assert "tools" not in model.requests[0][1]
    assert not executions and len(model.requests) == 1
    assert loop.run(payload) is loop.result
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_two_step_tool_loop_preserves_thinking_call_and_provider_pair():
    call = tool_call(provider_id="wire-id")
    loop, payload, model, executions, events = setup_run([
        ModelTurn("checking", "use a tool", (call,), "stop"), ModelTurn("answer", "done", (), "stop"),
    ])
    assert loop.run(payload).text == "answer"
    assert len(executions) == 1
    second = model.requests[1][1]["messages"]
    assert second[-2] == {"role": "assistant", "content": "checking", "thinking": "use a tool",
                          "tool_calls": [call.wire()]}
    assert second[-1] == {"role": "tool", "tool_name": "bash", "content": "result", "tool_call_id": "wire-id"}
    assert model.requests[0][0].step_id != model.requests[1][0].step_id
    assert model.requests[0][0].request_id != model.requests[1][0].request_id
    assert all(request.run_id == loop.context.request.run_id for request, _, _ in model.requests)
    assert [event.kind for event in events] == [RunEventKind.STARTED, RunEventKind.MODEL_STARTED,
        RunEventKind.MODEL_FINISHED, RunEventKind.TOOL_STARTED, RunEventKind.TOOL_FINISHED,
        RunEventKind.MODEL_STARTED, RunEventKind.MODEL_FINISHED, RunEventKind.FINISHED]
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_equal_calls_execute_in_order_without_fabricating_provider_ids():
    a, b = tool_call(), replace(tool_call(), local_call_id="second")
    loop, payload, model, executions, events = setup_run([
        ModelTurn("", "", (a, b), "stop"), ModelTurn("answer", "", (), "stop"),
    ])
    loop.run(payload)
    assert [context.call.local_call_id for _, context in executions] == [a.local_call_id, b.local_call_id]
    messages = model.requests[-1][1]["messages"]
    assert all("tool_call_id" not in message for message in messages if message["role"] == "tool")
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_transport_mutation_cannot_change_next_step_snapshot_or_schema():
    loop, payload, model, executions, events = setup_run([])
    def mutate(request, wire, deadline):
        wire["options"]["temperature"] = 99
        wire["tools"].clear()
        wire["messages"][-1]["content"] = "mutated"
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, turn=ModelTurn("", "", (tool_call(),), "stop"),
                          run_id=request.run_id, step_id=request.step_id, conversation_id=42)
    model.turns = [mutate, ModelTurn("answer", "", (), "stop")]
    loop.run(payload)
    second = model.requests[1][1]
    assert second["options"]["temperature"] == dict(loop.context.request.options)["temperature"]
    assert len(second["tools"]) == 2
    assert second["messages"][1]["content"] == "task"
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


@pytest.mark.parametrize("origin,agent_id", [("action", "general_assistant"), ("chat", "translator")])
def test_action_origin_wins_and_other_agents_are_zero_tools(origin, agent_id):
    request = ChatClient().create_request([], origin=origin, agent_id=agent_id)
    spec = ToolSpec.create("bash", lambda args, context: ToolOutput("should not run"))
    context = RunContext.create(request, tools=(spec,), tools_admitted=True)
    assert not context.tools and context.limits.max_model_rounds == 1
    with pytest.raises(ValueError):
        RunContext(request, (spec,), RunLimits())


def test_unadmitted_tools_never_reach_payload_or_execution():
    loop, payload, model, executions, events = setup_run([ModelTurn("", "", (tool_call(),), "stop")], tools=False)
    loop.run({**payload, "tools": [{"injected": True}]})
    assert "tools" not in model.requests[0][1]
    assert not executions
    assert_single_terminal(loop, events, ResultStatus.FAILED)


def test_admission_cannot_be_enabled_by_string_or_truthy_object():
    request = ChatClient().create_request([], agent_id="general_assistant")
    spec = ToolSpec.create("bash", lambda args, context: ToolOutput("unused"))
    for value in ("false", 1, object()):
        with pytest.raises(ValueError):
            RunContext.create(request, tools=(spec,), tools_admitted=value)


@pytest.mark.parametrize("status", [ResultStatus.SUCCEEDED, ResultStatus.LIMITED])
def test_length_never_continues_or_executes_even_if_adapter_marks_success(status):
    turn = ModelTurn("partial", "thinking", (tool_call(),), "length")
    loop, payload, model, executions, events = setup_run([{"status": status, "text": "partial", "turn": turn}])
    loop.run(payload)
    assert not executions and len(model.requests) == 1
    assert loop.result.text == "partial"
    assert_single_terminal(loop, events, ResultStatus.LIMITED)


def test_unknown_end_reason_is_protocol_failure():
    loop, payload, model, executions, events = setup_run([ModelTurn("partial", "", (tool_call(),), "unknown")])
    loop.run(payload)
    assert not executions and loop.result.error_code == ErrorCode.PROTOCOL
    assert_single_terminal(loop, events, ResultStatus.FAILED)


def test_cancel_before_run_skips_payload_preparation():
    loop, payload, model, executions, events = setup_run([])
    loop.cancelled.set()
    loop.run(lambda: pytest.fail("must not prepare cancelled images"))
    assert not model.requests and not executions
    assert_single_terminal(loop, events, ResultStatus.CANCELLED)


def test_cancel_after_model_step_skips_tools():
    loop, payload, model, executions, events = setup_run([ModelTurn("partial", "", (tool_call(),), "stop")])
    loop.on_event = lambda event: (events.append(event), loop.cancelled.set()
                                  if event.kind == RunEventKind.MODEL_FINISHED else None)
    loop.run(payload)
    assert not executions and len(model.requests) == 1
    assert loop.result.text == "partial"
    assert_single_terminal(loop, events, ResultStatus.CANCELLED)


def test_cancel_between_calls_skips_remaining_calls_and_next_model_step():
    def execute(args, context):
        context.cancelled.set()
        return ToolOutput("first result")
    loop, payload, model, executions, events = setup_run([
        ModelTurn("partial", "", (tool_call(), replace(tool_call(), local_call_id="second")), "stop"),
    ], executor=execute)
    loop.run(payload)
    assert len(executions) == len(model.requests) == 1
    assert_single_terminal(loop, events, ResultStatus.CANCELLED)


def test_executor_failure_is_finite_model_data_and_can_recover():
    def execute(args, context):
        raise RuntimeError("sk-secret-example")
    loop, payload, model, executions, events = setup_run([
        ModelTurn("", "", (tool_call(),), "stop"), ModelTurn("explain failure", "", (), "stop"),
    ], executor=execute)
    loop.run(payload)
    assert len(executions) == 1
    message = model.requests[1][1]["messages"][-1]
    assert "sk-secret" not in message["content"]
    assert any(e.kind == RunEventKind.TOOL_FINISHED and e.status == "failed" for e in events)
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


@pytest.mark.parametrize("call", [tool_call("unknown"), tool_call(args={"command": "pwd", "provider": "inject"}),
                                 tool_call("web_search", {"query": "q", "api_key": "inject"})])
def test_unknown_or_invalid_call_returns_paired_error_without_executor(call):
    loop, payload, model, executions, events = setup_run([
        ModelTurn("", "", (call,), "stop"), ModelTurn("answer", "", (), "stop"),
    ])
    loop.run(payload)
    assert not executions
    assert "参数无效" in model.requests[1][1]["messages"][-1]["content"]
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


@pytest.mark.parametrize("limits,calls", [(RunLimits(max_tool_calls=1), (tool_call(), tool_call())),
                                        (RunLimits(max_search_calls=1),
                                         (tool_call("web_search", {"query": "q"}),) * 2),
                                        (RunLimits(max_model_rounds=1), (tool_call(),))])
def test_budget_preflight_executes_no_partial_batch(limits, calls):
    loop, payload, model, executions, events = setup_run([ModelTurn("partial", "", calls, "stop")], limits=limits)
    loop.run(payload)
    assert not executions
    assert_single_terminal(loop, events, ResultStatus.LIMITED)


def test_round_limit_prevents_final_turn_side_effect():
    limits = RunLimits(max_model_rounds=2)
    loop, payload, model, executions, events = setup_run([
        ModelTurn("", "", (tool_call(),), "stop"), ModelTurn("partial", "", (tool_call(),), "stop"),
    ], limits=limits)
    loop.run(payload)
    assert len(model.requests) == 2 and len(executions) == 1
    assert_single_terminal(loop, events, ResultStatus.LIMITED)


def test_active_timeout_after_executor_is_limited_and_no_second_request():
    now = [0.0]
    def execute(args, context):
        assert context.deadline == 5.0
        now[0] = 6.0
        return ToolOutput("result")
    loop, payload, model, executions, events = setup_run([ModelTurn("partial", "", (tool_call(),), "stop")],
                                                        executor=execute, limits=RunLimits(active_seconds=5))
    loop.clock = lambda: now[0]
    loop.run(payload)
    assert len(model.requests) == 1
    assert_single_terminal(loop, events, ResultStatus.LIMITED)


def test_wrong_model_identity_fails_before_tool_execution():
    def wrong(request, payload, deadline):
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, turn=ModelTurn("", "", (tool_call(),), "stop"),
                          run_id=request.run_id, step_id="old", conversation_id=request.conversation_id)
    loop, payload, model, executions, events = setup_run([wrong])
    loop.run(payload)
    assert not executions and loop.result.error_code == ErrorCode.PROTOCOL
    assert_single_terminal(loop, events, ResultStatus.FAILED)


def test_context_budget_removes_whole_old_user_answer_block():
    messages = [Message("user", "old " + "a" * 8000), Message("assistant", "old answer"), Message("user", "current")]
    loop, payload, model, executions, events = setup_run([ModelTurn("answer", "", (), "stop")], messages=messages)
    loop.run(payload)
    assert model.requests[0][1]["messages"] == [payload["messages"][0], payload["messages"][-1]]
    assert any(e.kind == RunEventKind.CONTEXT_REDUCED for e in events)
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_required_current_context_too_large_makes_no_request():
    loop, payload, model, executions, events = setup_run([], messages=[Message("user", "a" * 8000)])
    loop.run(payload)
    assert not model.requests
    assert_single_terminal(loop, events, ResultStatus.LIMITED)


def test_complete_tool_block_trimming_never_leaves_dangling_calls_or_results():
    loop, payload, model, executions, events = setup_run([])
    block = ProtocolBlock.create([{"role": "assistant", "tool_calls": [tool_call().wire()]},
                                  {"role": "tool", "content": "old " + "a" * 8000}])
    required = ProtocolBlock.create([{"role": "user", "content": "current"}], required=True)
    blocks = [block, required]
    assert loop._trim(blocks, [spec.wire() for spec in loop.context.tools])
    assert blocks == [required]


def test_large_current_batch_shrinks_excerpts_while_preserving_all_pairs():
    calls = tuple(replace(tool_call(), local_call_id=f"call-{i}", provider_call_id=f"wire-{i}") for i in range(4))
    loop, payload, model, executions, events = setup_run([
        ModelTurn("checking", "plan", calls, "stop"), ModelTurn("answer", "", (), "stop"),
    ], executor=lambda args, context: ToolOutput("a" * 10000))
    loop.run(payload)
    assert len(executions) == 4 and len(model.requests) == 2
    messages = model.requests[-1][1]["messages"]
    assert len(messages[-5]["tool_calls"]) == 4
    assert [message["tool_call_id"] for message in messages[-4:]] == [f"wire-{i}" for i in range(4)]
    assert all(len(message["content"].encode()) <= 1024 for message in messages[-4:])
    assert any(event.kind == RunEventKind.CONTEXT_REDUCED for event in events)
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_explicit_context_rejection_gets_one_new_request_same_step():
    messages = [Message("user", "old"), Message("assistant", "old answer"), Message("user", "current")]
    failure = {"status": ResultStatus.FAILED, "error_code": ErrorCode.SERVER,
               "error": "input length 9000 exceeds context length 8192"}
    loop, payload, model, executions, events = setup_run([failure, ModelTurn("answer", "", (), "stop")],
                                                        tools=False, messages=messages)
    loop.run(payload)
    first, second = [request for request, _, _ in model.requests]
    assert first.step_id == second.step_id and first.request_id != second.request_id
    assert len(model.requests[1][1]["messages"]) == 2
    assert loop.model_rounds == 1 and loop.model_requests == 2
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_context_recovery_never_replays_a_completed_tool():
    failure = {"status": ResultStatus.FAILED, "error_code": ErrorCode.SERVER,
               "error": "input length 9000 exceeds context length 8192"}
    messages = [Message("user", "old"), Message("assistant", "old answer"), Message("user", "current")]
    loop, payload, model, executions, events = setup_run([
        ModelTurn("", "", (tool_call(),), "stop"), failure, ModelTurn("answer", "", (), "stop"),
    ], messages=messages)
    loop.run(payload)
    assert len(executions) == 1 and len(model.requests) == 3
    assert model.requests[1][0].step_id == model.requests[2][0].step_id
    assert model.requests[2][1]["messages"][-1]["role"] == "tool"
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


@pytest.mark.parametrize("code,error", [(ErrorCode.HTTP, "input length 9000 exceeds context length 8192"),
                                       (ErrorCode.SERVER, "context length error"), (ErrorCode.TIMEOUT, "timeout")])
def test_other_failures_are_not_retried(code, error):
    loop, payload, model, executions, events = setup_run([{"status": ResultStatus.FAILED,
                                                          "error_code": code, "error": error}])
    loop.run(payload)
    assert len(model.requests) == 1
    assert_single_terminal(loop, events, ResultStatus.FAILED)


def test_second_explicit_input_rejection_stops_without_third_request():
    failure = {"status": ResultStatus.FAILED, "error_code": ErrorCode.SERVER,
               "error": "input length 9000 exceeds context length 8192"}
    messages = [Message("user", "old"), Message("assistant", "answer"), Message("user", "current")]
    loop, payload, model, executions, events = setup_run([failure, failure], tools=False, messages=messages)
    loop.run(payload)
    assert len(model.requests) == 2
    assert_single_terminal(loop, events, ResultStatus.FAILED)


def test_stream_and_lifecycle_share_monotonic_sequence_and_reject_stale_step():
    loop, payload, model, executions, events = setup_run([])
    chunks = []
    def step(request, payload, deadline):
        event = StreamEvent(request.request_id, EventKind.CONTENT, "answer", run_id=request.run_id,
                            step_id=request.step_id, conversation_id=42, seq=1)
        loop.forward_stream(replace(event, step_id="old"), chunks.append)
        loop.forward_stream(event, chunks.append)
        loop.forward_stream(event, chunks.append)
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, "answer",
                          turn=ModelTurn("answer", "", (), "stop"), run_id=request.run_id,
                          step_id=request.step_id, conversation_id=42)
    model.turns.append(step)
    loop.run(payload)
    assert len(chunks) == 1
    event = next(e for e in events if e.kind == RunEventKind.CONTENT)
    assert chunks[0].seq == event.seq
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)


def test_utf8_tool_truncation_stays_within_byte_budget():
    for limit in (1, 128, 4096):
        result = bounded_tool_output(ToolOutput("猫🦉" * 1000), limit)
        assert len(result.text.encode()) <= limit
        assert "�" not in result.text


@pytest.mark.parametrize("kwargs", [{"max_model_rounds": 100}, {"max_tool_calls": True}, {"max_search_calls": 100},
                                    {"tool_result_bytes": 0}, {"active_seconds": float("nan")},
                                    {"active_seconds": 301}, {"context_reserve": -1}])
def test_invalid_run_limits(kwargs):
    with pytest.raises(ValueError):
        RunLimits(**kwargs)


@pytest.mark.parametrize('name,count', [('bash', 17), ('web_search', 8)])
def test_configurable_limits_execute_beyond_old_hidden_caps(name, count):
    args = {'command': 'pwd'} if name == 'bash' else {'query': 'documentation'}
    turns = [ModelTurn('', '', (tool_call(name, args),), 'stop') for _ in range(count)]
    turns.append(ModelTurn('completed', '', (), 'stop'))
    loop, payload, model, executions, events = setup_run(
        turns, limits=RunLimits(max_model_rounds=99, max_tool_calls=99, max_search_calls=99))
    loop.run(payload)
    assert_single_terminal(loop, events, ResultStatus.SUCCEEDED)
    assert len(executions) == count and len(model.requests) == count + 1


def test_provider_type_and_index_are_preserved():
    assembler = TurnAssembler()
    assembler.accept({"message": {"tool_calls": [{"type": "function", "function": {
        "name": "bash", "index": 0, "arguments": {"command": "pwd"}}}]}, "done": True})
    wire = assembler.turn.wire()["tool_calls"][0]
    assert wire["type"] == "function" and wire["function"]["index"] == 0
    assert "id" not in wire


@pytest.mark.parametrize("args", [{"command": "pwd", "timeout": True}, {"command": "pwd", "timeout": 121},
                                 {"command": "\x00"}, {"query": ""}, {"query": "q", "objective": None}])
def test_invalid_typed_tool_arguments(args):
    call = tool_call("web_search" if "query" in args else "bash", args)
    with pytest.raises(ValueError):
        validate_tool_arguments(call)


def test_initial_blocks_pin_all_system_messages_and_latest_user():
    blocks = initial_blocks([{"role": "system", "content": "A"}, {"role": "system", "content": "B"},
                             {"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"},
                             {"role": "user", "content": "latest"}])
    assert [block.required for block in blocks] == [True, True, False, True]
