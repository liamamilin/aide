import json
from dataclasses import replace

import pytest

from ai_desktop.llm.chat_client import ChatClient
from ai_desktop.llm.events import EventKind, ResultStatus
from ai_desktop.llm.ollama_protocol import StreamProtocolError, TurnAssembler
from ai_desktop.llm.qt_stream import NDJSONDecoder, QtChatTransport
from ai_desktop.llm.run_worker import RunWorker


def call(name="bash", args=None, provider_id=None):
    value = {"function": {"name": name, "arguments": args or {"command": "pwd"}}}
    if provider_id is not None:
        value["id"] = provider_id
    return value


def test_assemble_mixed_multiple_and_equal_calls_without_deduplication():
    assembler = TurnAssembler()
    assembler.accept({"message": {"thinking": "plan", "content": "before", "tool_calls": [call()]}})
    assembler.accept({"message": {"content": "after", "tool_calls": [
        call(), call("web_search", {"query": "docs"}, "p3")]},
                      "done": True, "done_reason": "stop", "eval_count": 12})
    turn = assembler.turn
    assert turn.content == "beforeafter" and turn.thinking == "plan"
    assert [c.name for c in turn.tool_calls] == ["bash", "bash", "web_search"]
    assert len({c.local_call_id for c in turn.tool_calls}) == 3
    wire = turn.wire()
    assert "id" not in wire["tool_calls"][0]
    assert wire["tool_calls"][2]["id"] == "p3"
    assert dict(turn.metrics) == {"eval_count": 12}
    wire["tool_calls"][0]["function"]["arguments"]["command"] = "mutated"
    assert turn.wire()["tool_calls"][0]["function"]["arguments"]["command"] == "pwd"


@pytest.mark.parametrize("message", [{"role": "user"}, {"tool_calls": None}, {"tool_calls": [{}]},
                                      {"tool_calls": [call(args={"x": float("nan")})]},
                                      {"tool_calls": [{"function": {"name": "bash", "arguments": '{"com'}}]},
                                      {"tool_calls": [{"id": "", "function": {"name": "bash", "arguments": {}}}]}])
def test_invalid_calls_are_protocol_errors_not_executable_fragments(message):
    with pytest.raises(StreamProtocolError):
        TurnAssembler().accept({"message": message})


def test_repeated_provider_id_rejected_but_identical_call_without_id_kept():
    assembler = TurnAssembler()
    assembler.accept({"message": {"tool_calls": [call(provider_id="one")]}})
    with pytest.raises(StreamProtocolError):
        assembler.accept({"message": {"tool_calls": [call(provider_id="one")]}})


@pytest.mark.parametrize("tail", [{"done_reason": "unknown"}, {"eval_count": -1}, {"eval_count": True}])
def test_terminal_validation(tail):
    with pytest.raises(StreamProtocolError):
        TurnAssembler().accept({"message": {}, "done": True, **tail})


def test_decoder_retains_turn_and_scoped_monotonic_events():
    request = ChatClient().create_request([], conversation_id=42)
    raw = (json.dumps({"message": {"thinking": "先思考", "content": "回答"}}) + "\n" +
           json.dumps({"done": True, "done_reason": "length", "eval_count": 1}) + "\n").encode()
    decoder = NDJSONDecoder(request)
    events = []
    for byte in raw:
        events.extend(decoder.feed(bytes([byte])))
    assert [e.kind for e in events] == [EventKind.THINKING, EventKind.CONTENT, EventKind.LIMITED]
    assert [e.seq for e in events] == [1, 2, 3]
    assert all(request.matches(e) for e in events)
    assert events[-1].turn.content == "回答"
    assert not list(decoder.feed(b'{}\n', final=True))


def test_buffer_and_turn_bounds(monkeypatch):
    monkeypatch.setattr(TurnAssembler, "MAX_BYTES", 128)
    with pytest.raises(StreamProtocolError):
        list(NDJSONDecoder("id").feed(b"a" * 129))
    assembler = TurnAssembler()
    assembler.accept({"message": {"content": "a" * 50}})
    with pytest.raises(StreamProtocolError):
        assembler.accept({"message": {"content": "a" * 100}})


def test_request_origin_and_same_request_wrong_step():
    request = ChatClient().create_request([], origin="action", action_id="translate", conversation_id=42)
    assert request.origin == "action" and request.action_id == "translate"
    assert request.run_id and request.step_id
    event = list(NDJSONDecoder(request).feed(b'{"message":{"content":"x"}}\n'))[0]
    assert request.matches(event)
    assert not request.matches(replace(event, step_id="stale"))
    assert not request.matches(replace(event, conversation_id=43))
    with pytest.raises(ValueError):
        ChatClient().create_request([], origin="chat", action_id="translate")


def test_worker_duplicate_or_other_step_event_rejected(qapp):
    worker = RunWorker([], "", conversation_id=42)
    event = list(NDJSONDecoder(worker.request).feed(b'{"message":{"content":"x"}}\n'))[0]
    assert not worker.accept_event(replace(event, step_id="stale"))
    assert worker.accept_event(event)
    assert not worker.accept_event(event)
    worker.release_attachments()
    worker.deleteLater()


@pytest.mark.parametrize("reason,tools,status", [("length", True, ResultStatus.LIMITED),
                                                ("stop", True, ResultStatus.FAILED),
                                                ("stop", False, ResultStatus.SUCCEEDED)])
def test_qt_terminal_tools_are_never_executed(qtbot, ollama_server, reason, tools, status):
    message = {"content": "partial", "thinking": "reasoning"}
    if tools:
        message["tool_calls"] = [call()]
    ollama_server.enqueue({"message": message, "done": True, "done_reason": reason})
    request = ChatClient().create_request([], conversation_id=42, origin="action", action_id="translate")
    transport = QtChatTransport(request)
    with qtbot.waitSignal(transport.done, timeout=3000) as signal:
        transport.start(b'{}')
    result = signal.args[0]
    assert result.status == status
    assert result.text == "partial" and result.turn.thinking == "reasoning"
    assert request.matches(result)
    assert len(ollama_server.requests) == 1
    transport.deleteLater()
