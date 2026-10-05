"""Real Qt threads and loopback HTTP regression tests."""
import json
import socket
from dataclasses import replace

import pytest

from ai_desktop.llm.events import ErrorCode, ResultStatus
from ai_desktop.llm.run_worker import RunWorker
from ai_desktop.utils.storage import Message


def run_and_wait(qtbot, worker):
    completed = []
    worker.done.connect(completed.append)
    worker.start()
    try:
        qtbot.waitUntil(lambda: bool(completed), timeout=3000)
    finally:
        worker.cancel()
        assert worker.wait(2000)
    assert len(completed) == 1
    return completed[0]


def test_task_limits_are_frozen_at_worker_creation(qapp, tmp_db, tmp_path, monkeypatch):
    from ai_desktop import config
    from ai_desktop.services.execution_context import ExecutionSnapshot

    monkeypatch.setattr(config, 'TASK_MAX_MODEL_ROUNDS', 33)
    monkeypatch.setattr(config, 'TASK_MAX_TOOL_CALLS', 42)
    monkeypatch.setattr(config, 'TASK_MAX_SEARCH_CALLS', 21)
    worker = RunWorker([], 'SYSTEM', agent_id='general_assistant', tools_admitted=True,
                       execution=ExecutionSnapshot.create(tmp_path))
    monkeypatch.setattr(config, 'TASK_MAX_TOOL_CALLS', 2)
    assert worker.context.limits.max_tool_calls == 42
    assert worker.context.limits.max_search_calls == 21
    assert worker.context.limits.max_model_rounds == 33
    ordinary = RunWorker([], 'SYSTEM', agent_id='general_assistant')
    assert ordinary.context.limits.max_model_rounds == 1 and not ordinary.context.tools
    worker.deleteLater()
    ordinary.deleteLater()


@pytest.mark.parametrize("text", [
    "无法确定原因，可以先检查日志。", "HTTP 协议是应用层协议。", "响应超时通常可以重试。",
])
def test_normal_reply_is_success_even_with_error_like_prefix(qtbot, ollama_server, text):
    ollama_server.enqueue({"message": {"content": text}, "done": True})
    worker = RunWorker([Message("user", "解释一下")], "SYSTEM")
    result = run_and_wait(qtbot, worker)
    assert result.text == text
    assert result.ok
    assert result.request_id == worker.request.request_id


def test_service_error_inside_http_200_is_failure(qtbot, ollama_server):
    ollama_server.enqueue({"error": "invalid image payload"})
    result = run_and_wait(qtbot, RunWorker([], "SYSTEM"))
    assert not result.ok
    assert result.error == "invalid image payload"
    assert result.error_code == ErrorCode.SERVER
    assert result.text == ""


def test_unexpected_eof_does_not_save_partial_answer_as_success(qtbot, ollama_server):
    ollama_server.enqueue({"message": {"content": "partial"}})
    result = run_and_wait(qtbot, RunWorker([], "SYSTEM"))
    assert not result.ok
    assert result.text == "partial"
    assert result.error_code == ErrorCode.PROTOCOL


def test_missing_image_finishes_without_posting_or_raising(qtbot, ollama_server, tmp_path):
    worker = RunWorker([Message("user", "image", images=[str(tmp_path / "missing.png")])], "SYSTEM")
    result = run_and_wait(qtbot, worker)
    assert ollama_server.requests == []
    assert not result.ok
    assert result.error_code == ErrorCode.IMAGE


def test_request_uses_submission_snapshot(qtbot, ollama_server, monkeypatch):
    from ai_desktop import config
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT", 17)
    monkeypatch.setattr(config, "OLLAMA_THINK", False)
    monkeypatch.setattr(config, "OLLAMA_TEMPERATURE", 0.2)
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 4096)
    monkeypatch.setattr(config, "OLLAMA_KEEP_ALIVE", "5m")
    message = Message("user", "original", id=42)
    worker = RunWorker([message], "SYSTEM", "submitted-model", conversation_id=7, agent_id="translator",
                                 think=False)
    message.content = "edited later"
    message.images.append("later.png")
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://changed:11434")
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT", 99)
    monkeypatch.setattr(config, "OLLAMA_THINK", True)
    monkeypatch.setattr(config, "OLLAMA_TEMPERATURE", 1.5)
    monkeypatch.setattr(config, "OLLAMA_NUM_CTX", 8192)
    monkeypatch.setattr(config, "OLLAMA_KEEP_ALIVE", "30m")
    ollama_server.enqueue({"message": {"content": "ok"}, "done": True})
    assert run_and_wait(qtbot, worker).ok
    request = ollama_server.requests[0]
    assert request["path"] == "/api/chat"
    payload = request["payload"]
    assert worker.request.base_url == ollama_server.url
    assert worker.request.timeout == 17
    assert payload["model"] == "submitted-model"
    assert payload["think"] is False
    assert payload["keep_alive"] == "5m"
    assert payload["options"]["temperature"] == 0.2
    assert payload["options"]["num_ctx"] == 4096
    assert payload["messages"] == [
        {"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "original"},
    ]
    assert worker.request.conversation_id == 7
    assert worker.request.agent_id == "translator"
    assert worker.request.messages[0].id == 42


def test_cancel_before_start_makes_no_http_request(qtbot, ollama_server):
    worker = RunWorker([], "SYSTEM")
    worker.cancel()
    result = run_and_wait(qtbot, worker)
    assert ollama_server.requests == []
    assert result.status == ResultStatus.CANCELLED


def test_unified_worker_pre_cancel_emits_one_run_terminal(qtbot, ollama_server):
    from ai_desktop.llm.run_types import RunEventKind
    from ai_desktop.llm.run_worker import RunWorker
    worker = RunWorker([], "SYSTEM", agent_id="general_assistant")
    events = []
    worker.run_event.connect(events.append)
    worker.cancel()
    result = run_and_wait(qtbot, worker)
    assert result.status == ResultStatus.CANCELLED
    assert [event.kind for event in events] == [RunEventKind.STARTED, RunEventKind.FINISHED]
    assert events[-1].status == "cancelled"
    assert not ollama_server.requests


def test_action_worker_forces_single_round_empty_tools_and_shared_sequences(qtbot, ollama_server):
    from ai_desktop.llm.run_types import RunEventKind
    worker = RunWorker([], "SYSTEM", agent_id="general_assistant", origin="action", action_id="translate")
    events, chunks = [], []
    worker.run_event.connect(events.append)
    worker.content_event.connect(chunks.append)
    ollama_server.enqueue({"message": {"thinking": "plan", "content": "answer"}, "done": True})
    result = run_and_wait(qtbot, worker)
    assert result.ok
    assert len(ollama_server.requests) == 1 and "tools" not in ollama_server.requests[0]["payload"]
    assert worker.context.tools == () and worker.context.limits.max_model_rounds == 1
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert chunks[0].seq == next(event.seq for event in events if event.kind == RunEventKind.CONTENT)


@pytest.mark.parametrize("before_headers,partial", [(True, ""), (False, ""), (False, "partial")])
def test_cancel_releases_connection_without_waiting_for_token(qtbot, ollama_server, before_headers, partial):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []),
        before_headers=before_headers, hold_open=True,
    )
    worker = RunWorker([], "SYSTEM")
    completed, chunks = [], []
    worker.done.connect(completed.append)
    worker.content_event.connect(lambda event: chunks.append(event.text))
    worker.start()
    try:
        qtbot.waitUntil(scenario.received.is_set)
        if not before_headers:
            qtbot.waitUntil(scenario.sent.is_set)
        if partial:
            qtbot.waitUntil(lambda: bool(chunks))
        worker.cancel()
        worker.cancel()
        qtbot.waitUntil(lambda: bool(completed), timeout=1000)
        qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    finally:
        worker.cancel()
        assert worker.wait(2000)
    assert len(completed) == 1
    assert completed[0].status == ResultStatus.CANCELLED
    assert completed[0].text == partial


def test_duplicate_completion_is_ignored_and_connection_released(qtbot, ollama_server):
    scenario = ollama_server.enqueue(
        {"message": {"content": "answer"}, "done": True},
        {"message": {"content": "extra answer"}, "done": True}, hold_open=True,
    )
    worker = RunWorker([], "SYSTEM")
    chunks = []
    worker.content_event.connect(lambda event: chunks.append(event.text))
    assert run_and_wait(qtbot, worker).ok
    qtbot.waitUntil(scenario.disconnected.is_set)
    assert chunks == ["answer"]


def test_utf8_split_into_individual_bytes_and_final_line_without_newline(qtbot, ollama_server):
    body = json.dumps({"message": {"thinking": "想🤔", "content": "你好🌍"}, "done": True},
                      ensure_ascii=False).encode()
    ollama_server.enqueue(chunks=[bytes([byte]) for byte in body], delay=0.001)
    worker = RunWorker([], "SYSTEM")
    thinking = []
    worker.thinking_event.connect(lambda event: thinking.append(event.text))
    result = run_and_wait(qtbot, worker)
    assert result.ok and result.text == "你好🌍"
    assert thinking == ["想🤔"]


@pytest.mark.parametrize("chunks,code", [
    ([b'not json\n'], ErrorCode.PROTOCOL),
    ([b'{"message":{"content":null}}\n'], ErrorCode.PROTOCOL),
    ([b'\xff\n'], ErrorCode.PROTOCOL),
    ([b'{"error":"broken"}\n'], ErrorCode.SERVER),
])
def test_malformed_stream_fails_and_closes_connection(qtbot, ollama_server, chunks, code):
    scenario = ollama_server.enqueue(chunks=chunks, hold_open=True)
    result = run_and_wait(qtbot, RunWorker([], "SYSTEM"))
    assert result.error_code == code
    qtbot.waitUntil(scenario.disconnected.is_set)


@pytest.mark.parametrize("status", [302, 404, 500])
def test_http_status_is_reported_separately(qtbot, ollama_server, status):
    ollama_server.enqueue(chunks=[b'upstream failed'], status=status)
    result = run_and_wait(qtbot, RunWorker([], "SYSTEM"))
    assert result.error_code == ErrorCode.HTTP
    assert result.error == f"HTTP {status}"


@pytest.mark.parametrize("before_headers,partial", [(True, ""), (False, ""), (False, "partial")])
def test_idle_timeout_before_first_token_and_during_stream(qtbot, ollama_server, before_headers, partial):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []),
        before_headers=before_headers, hold_open=True,
    )
    worker = RunWorker([], "SYSTEM")
    worker.request = replace(worker.request, timeout=0.15)
    result = run_and_wait(qtbot, worker)
    assert result.error_code == ErrorCode.TIMEOUT
    assert result.text == partial
    assert "等待" in result.error
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_active_stream_can_outlive_connection_and_idle_budgets(qtbot, ollama_server):
    ollama_server.enqueue(*[{"message": {"content": "x"}}] * 8, {"done": True}, delay=0.04)
    worker = RunWorker([], "SYSTEM")
    worker.request = replace(worker.request, timeout=0.2, connect_timeout=0.1)
    result = run_and_wait(qtbot, worker)
    assert result.ok and result.text == "xxxxxxxx"


def test_truncated_http_body_is_not_success(qtbot, ollama_server):
    ollama_server.enqueue({"message": {"content": "partial"}}, content_length=10000)
    result = run_and_wait(qtbot, RunWorker([], "SYSTEM"))
    assert result.error_code == ErrorCode.PROTOCOL
    assert result.text == "partial"


def test_connection_refused_is_not_a_protocol_or_timeout_error(qtbot):
    with socket.socket() as reserved:
        # Release a temporary port before connecting: macOS can silently hold
        # SYNs for a bound-but-not-listening socket instead of refusing them.
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    worker = RunWorker([], "SYSTEM")
    worker.request = replace(worker.request, base_url=f"http://127.0.0.1:{port}")
    result = run_and_wait(qtbot, worker)
    assert result.error_code == ErrorCode.CONNECTION
    assert not result.ok


@pytest.mark.parametrize('parts,expected', [
    (['Hello', ' world'], 'Hello world'),
    (['line1\n', 'line2'], 'line1\nline2'),
])
def test_incremental_content_is_delivered_with_step_identity(qtbot, ollama_server, parts, expected):
    ollama_server.enqueue(*[{'message': {'content': part}} for part in parts], {'done': True})
    worker = RunWorker([], 'SYSTEM')
    events = []
    worker.content_event.connect(events.append)
    result = run_and_wait(qtbot, worker)
    assert result.ok and result.text == expected
    assert [event.text for event in events] == parts
    assert all(worker.request.matches(event) for event in events)
    assert len({event.seq for event in events}) == len(parts)


@pytest.mark.parametrize('data', [[], None, {'message': None}, {'message': {'content': 7}},
                                 {'message': {}, 'done': 'false'}, {'error': []}, {'unexpected': 'field'}])
def test_malformed_response_closes_real_transport_without_success(qtbot, ollama_server, data):
    scenario = ollama_server.enqueue(chunks=[json.dumps(data).encode()+b'\n'], hold_open=True)
    result = run_and_wait(qtbot, RunWorker([], 'SYSTEM'))
    assert result.status == ResultStatus.FAILED and result.error_code == ErrorCode.PROTOCOL
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_empty_response_is_not_a_successful_answer(qtbot, ollama_server):
    ollama_server.enqueue(chunks=[b'\n\n'])
    result = run_and_wait(qtbot, RunWorker([], 'SYSTEM'))
    assert result.status == ResultStatus.FAILED and result.error_code == ErrorCode.PROTOCOL


def test_thinking_and_content_use_identity_events_and_one_final_result(qtbot, ollama_server):
    ollama_server.enqueue({'message': {'thinking': 'reasoning'}},
                          {'message': {'content': 'answer'}, 'done': True})
    worker = RunWorker([], 'SYSTEM')
    thoughts, contents = [], []
    worker.thinking_event.connect(thoughts.append)
    worker.content_event.connect(contents.append)
    result = run_and_wait(qtbot, worker)
    assert result.ok and result.text == 'answer' and result.turn.thinking == 'reasoning'
    assert [event.text for event in thoughts] == ['reasoning']
    assert [event.text for event in contents] == ['answer']
    assert all(worker.request.matches(event) for event in thoughts+contents)


def test_length_with_ungranted_tool_never_continues_or_executes(qtbot, ollama_server, tmp_path):
    artifact = tmp_path/'must-not-exist'
    ollama_server.enqueue({'message': {'content': 'partial', 'tool_calls': [{'function':
                          {'name': 'bash', 'arguments': {'command': f'touch {artifact}'}}}]},
                          'done': True, 'done_reason': 'length'})
    worker = RunWorker([], 'SYSTEM', origin='action', action_id='translate', agent_id='general_assistant')
    result = run_and_wait(qtbot, worker)
    assert result.status == ResultStatus.LIMITED and result.text == 'partial'
    assert not artifact.exists() and len(ollama_server.requests) == 1


@pytest.mark.parametrize('kind,expected', [('connect', ErrorCode.TIMEOUT), ('read', ErrorCode.TIMEOUT),
                                        ('connection', ErrorCode.CONNECTION), ('internal', ErrorCode.INTERNAL)])
def test_shared_preparation_error_mapping_preserves_classification(kind, expected):
    import requests
    from urllib3.exceptions import ReadTimeoutError

    from ai_desktop.llm.chat_client import _exception_error
    errors = {'connect': requests.exceptions.ConnectTimeout(),
              'read': requests.exceptions.ConnectionError(ReadTimeoutError(None, '/api/chat', 'Read timed out')),
              'connection': requests.exceptions.ConnectionError(), 'internal': RuntimeError()}
    assert _exception_error(errors[kind])[0] == expected
