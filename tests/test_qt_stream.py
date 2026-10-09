"""Framing boundaries and the transport's connection-phase timer."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest
from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtNetwork import QNetworkReply

from ai_desktop.llm.chat_client import ChatClient, StreamProtocolError
from ai_desktop.llm.events import ErrorCode, EventKind, ResultStatus
from ai_desktop.llm.qt_stream import NDJSONDecoder, QtChatTransport


def test_every_possible_utf8_packet_boundary():
    body = (json.dumps({"message": {"thinking": "思考🤔", "content": "你好🌍"}}, ensure_ascii=False)
            + '\r\n\n{"done":true}').encode()
    expected = [(EventKind.THINKING, "思考🤔"), (EventKind.CONTENT, "你好🌍"), (EventKind.COMPLETE, "")]
    for boundary in range(len(body) + 1):
        decoder = NDJSONDecoder("request")
        events = list(decoder.feed(body[:boundary]))
        events.extend(decoder.feed(body[boundary:], final=True))
        assert [(event.kind, event.text) for event in events] == expected
        assert {event.request_id for event in events} == {"request"}
        assert list(decoder.feed(b'garbage after complete\n', final=True)) == []


def test_valid_line_is_delivered_before_later_malformed_line_in_same_packet():
    decoder = NDJSONDecoder("request")
    events = decoder.feed(b'{"message":{"content":"partial"}}\nBAD\n')
    assert next(events).text == "partial"
    with pytest.raises(json.JSONDecodeError):
        next(events)


@pytest.mark.parametrize("data", [b"", b"\r\n ", b'{"message":{"content":"partial"}}'])
def test_eof_without_done_is_failure(data):
    with pytest.raises(StreamProtocolError):
        list(NDJSONDecoder("request").feed(data, final=True))


def test_connection_timeout_aborts_a_reply_with_no_upload_or_response(qtbot):
    # A deterministic disconnected reply: external blackhole addresses would
    # make this connection-phase test depend on the machine's network routing.
    aborts = []

    class PendingReply(QNetworkReply):
        def abort(self):
            aborts.append(True)
            self.setFinished(True)
            self.finished.emit()

    request = replace(ChatClient().create_request([]), connect_timeout=0.04, timeout=10)
    transport = QtChatTransport(request)
    reply = PendingReply(transport)
    completed = []
    transport.done.connect(completed.append)
    with patch.object(transport._manager, "post", return_value=reply):
        transport.start(b"{}")
        qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    transport.cancel()
    assert len(completed) == 1
    assert aborts == [True]
    assert completed[0].status == ResultStatus.FAILED
    assert completed[0].error_code == ErrorCode.TIMEOUT
    assert "连接 Ollama 超时" in completed[0].error
    transport.deleteLater()


@pytest.mark.parametrize("partial", ["", "partial answer"])
@pytest.mark.parametrize("notification", ["destroyed_signal", "late_headers"])
def test_destroyed_active_reply_finishes_once_without_reading_a_stale_wrapper(
    qtbot, ollama_server, partial, notification,
):
    scenario = ollama_server.enqueue(
        *([{"message": {"content": partial}}] if partial else []),
        before_headers=not partial, hold_open=True,
    )
    transport = QtChatTransport(ChatClient().create_request([]))
    completed, chunks = [], []
    transport.done.connect(completed.append)
    transport.stream_event.connect(chunks.append)
    try:
        transport.start(b"{}")
        qtbot.waitUntil(scenario.received.is_set)
        if partial:
            qtbot.waitUntil(lambda: any(event.kind == EventKind.CONTENT for event in chunks))
        reply = transport._reply
        if notification == "late_headers":
            # A queued destroyed notification may arrive after another callback.
            reply.destroyed.disconnect(transport._reply_destroyed)
        reply.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(reply)
        if notification == "late_headers":
            transport._headers_received()
        qtbot.waitUntil(lambda: bool(completed), timeout=500)
        # These are the exact late callbacks seen in the full-suite failure.
        transport._headers_received()
        transport._ready_read()
        transport._finished()
        transport.cancel()
        assert len(completed) == 1
        assert completed[0].status == ResultStatus.FAILED
        assert completed[0].error_code == ErrorCode.CONNECTION
        assert completed[0].text == partial
        assert not transport._connect_timer.isActive() and not transport._idle_timer.isActive()
        assert transport._reply is None
        qtbot.waitUntil(scenario.disconnected.is_set)
    finally:
        # Keep the pre-fix red run from leaving a dangling native reply timer.
        transport._reply = None
        transport.cancel()
        transport.deleteLater()


def test_cancel_with_deleted_reply_and_delayed_destroyed_notification(qtbot, ollama_server):
    scenario = ollama_server.enqueue(before_headers=True)
    transport = QtChatTransport(ChatClient().create_request([]))
    completed = []
    transport.done.connect(completed.append)
    try:
        transport.start(b"{}")
        qtbot.waitUntil(scenario.received.is_set)
        reply = transport._reply
        reply.destroyed.disconnect(transport._reply_destroyed)
        reply.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(reply)
        transport.cancel()
        transport._headers_received()
        transport._ready_read()
        transport._finished()
        assert len(completed) == 1 and completed[0].status == ResultStatus.CANCELLED
        assert transport._reply is None
        assert not transport._connect_timer.isActive() and not transport._idle_timer.isActive()
        qtbot.waitUntil(scenario.disconnected.is_set)
    finally:
        transport.cancel()
        transport.deleteLater()


def test_completed_reply_is_released_before_late_callbacks_and_cancel(qtbot, ollama_server):
    ollama_server.enqueue({"message": {"content": "answer"}, "done": True})
    transport = QtChatTransport(ChatClient().create_request([]))
    completed = []
    transport.done.connect(completed.append)
    try:
        transport.start(b"{}")
        reply = transport._reply
        qtbot.waitUntil(lambda: bool(completed))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(reply)
        transport._headers_received()
        transport._ready_read()
        transport._finished()
        transport.cancel()
        assert len(completed) == 1 and completed[0].ok
        assert completed[0].text == "answer"
        assert transport._reply is None
    finally:
        transport.cancel()
        transport.deleteLater()
