"""Real Qt threads and loopback HTTP regression tests."""
import json
import socket
from dataclasses import replace

import pytest

from ai_desktop.llm.events import ErrorCode, ResultStatus
from ai_desktop.main import StreamingChatWorker
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


@pytest.mark.parametrize("text", [
    "无法确定原因，可以先检查日志。", "HTTP 协议是应用层协议。", "响应超时通常可以重试。",
])
def test_normal_reply_is_success_even_with_error_like_prefix(qtbot, ollama_server, text):
    ollama_server.enqueue({"message": {"content": text}, "done": True})
    worker = StreamingChatWorker([Message("user", "解释一下")], "SYSTEM")
    result = run_and_wait(qtbot, worker)
    assert result.text == text
    assert result.ok
    assert result.request_id == worker.request.request_id


def test_service_error_inside_http_200_is_failure(qtbot, ollama_server):
    ollama_server.enqueue({"error": "invalid image payload"})
    result = run_and_wait(qtbot, StreamingChatWorker([], "SYSTEM"))
    assert not result.ok
    assert result.error == "invalid image payload"
    assert result.error_code == ErrorCode.SERVER
    assert result.text == ""


def test_unexpected_eof_does_not_save_partial_answer_as_success(qtbot, ollama_server):
    ollama_server.enqueue({"message": {"content": "partial"}})
    result = run_and_wait(qtbot, StreamingChatWorker([], "SYSTEM"))
    assert not result.ok
    assert result.text == "partial"
    assert result.error_code == ErrorCode.PROTOCOL


def test_missing_image_finishes_without_posting_or_raising(qtbot, ollama_server, tmp_path):
    worker = StreamingChatWorker([Message("user", "image", images=[str(tmp_path / "missing.png")])], "SYSTEM")
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
    worker = StreamingChatWorker([message], "SYSTEM", "submitted-model", conversation_id=7, agent_id="translator")
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
    worker = StreamingChatWorker([], "SYSTEM")
    worker.cancel()
    result = run_and_wait(qtbot, worker)
    assert ollama_server.requests == []
    assert result.status == ResultStatus.CANCELLED


@pytest.mark.parametrize("before_headers,partial", [(True, ""), (False, ""), (False, "partial")])
def test_cancel_releases_connection_without_waiting_for_token(qtbot, ollama_server, before_headers, partial):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []),
        before_headers=before_headers, hold_open=True,
    )
    worker = StreamingChatWorker([], "SYSTEM")
    completed, chunks = [], []
    worker.done.connect(completed.append)
    worker.chunk.connect(chunks.append)
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
    worker = StreamingChatWorker([], "SYSTEM")
    chunks = []
    worker.chunk.connect(chunks.append)
    assert run_and_wait(qtbot, worker).ok
    qtbot.waitUntil(scenario.disconnected.is_set)
    assert chunks == ["answer"]


def test_utf8_split_into_individual_bytes_and_final_line_without_newline(qtbot, ollama_server):
    body = json.dumps({"message": {"thinking": "想🤔", "content": "你好🌍"}, "done": True},
                      ensure_ascii=False).encode()
    ollama_server.enqueue(chunks=[bytes([byte]) for byte in body], delay=0.001)
    worker = StreamingChatWorker([], "SYSTEM")
    thinking = []
    worker.thinking_chunk.connect(thinking.append)
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
    result = run_and_wait(qtbot, StreamingChatWorker([], "SYSTEM"))
    assert result.error_code == code
    qtbot.waitUntil(scenario.disconnected.is_set)


@pytest.mark.parametrize("status", [302, 404, 500])
def test_http_status_is_reported_separately(qtbot, ollama_server, status):
    ollama_server.enqueue(chunks=[b'upstream failed'], status=status)
    result = run_and_wait(qtbot, StreamingChatWorker([], "SYSTEM"))
    assert result.error_code == ErrorCode.HTTP
    assert result.error == f"HTTP {status}"


@pytest.mark.parametrize("before_headers,partial", [(True, ""), (False, ""), (False, "partial")])
def test_idle_timeout_before_first_token_and_during_stream(qtbot, ollama_server, before_headers, partial):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []),
        before_headers=before_headers, hold_open=True,
    )
    worker = StreamingChatWorker([], "SYSTEM")
    worker.request = replace(worker.request, timeout=0.15)
    result = run_and_wait(qtbot, worker)
    assert result.error_code == ErrorCode.TIMEOUT
    assert result.text == partial
    assert "等待" in result.error
    qtbot.waitUntil(scenario.disconnected.is_set)


def test_active_stream_can_outlive_connection_and_idle_budgets(qtbot, ollama_server):
    ollama_server.enqueue(*[{"message": {"content": "x"}}] * 8, {"done": True}, delay=0.04)
    worker = StreamingChatWorker([], "SYSTEM")
    worker.request = replace(worker.request, timeout=0.2, connect_timeout=0.1)
    result = run_and_wait(qtbot, worker)
    assert result.ok and result.text == "xxxxxxxx"


def test_truncated_http_body_is_not_success(qtbot, ollama_server):
    ollama_server.enqueue({"message": {"content": "partial"}}, content_length=10000)
    result = run_and_wait(qtbot, StreamingChatWorker([], "SYSTEM"))
    assert result.error_code == ErrorCode.PROTOCOL
    assert result.text == "partial"


def test_connection_refused_is_not_a_protocol_or_timeout_error(qtbot):
    with socket.socket() as reserved:
        # Release a temporary port before connecting: macOS can silently hold
        # SYNs for a bound-but-not-listening socket instead of refusing them.
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    worker = StreamingChatWorker([], "SYSTEM")
    worker.request = replace(worker.request, base_url=f"http://127.0.0.1:{port}")
    result = run_and_wait(qtbot, worker)
    assert result.error_code == ErrorCode.CONNECTION
    assert not result.ok
