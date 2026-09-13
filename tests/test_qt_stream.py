"""Framing boundaries and the transport's connection-phase timer."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest
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
