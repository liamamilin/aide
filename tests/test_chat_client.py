"""LLM chat client tests — mock HTTP responses"""
import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.llm.events import ErrorCode, EventKind
from ai_desktop.utils.storage import Message

# 1x1 透明 PNG
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _fake_stream_response(lines: list[str]):
    """构建一个 mock requests.Response，iter_lines 返回指定行"""
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = lines
    return resp


def _stream():
    return ChatClient("http://localhost", "model", 10).chat_stream([])


def _event_pairs(stream):
    return [(event.kind.value, event.text) for event in stream]


class TestChatStream:
    """chat_stream 迭代器测试"""

    def test_response_tokens(self):
        lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            results = _event_pairs(_stream())
            assert results == [("content", "Hello"), ("content", " world"), ("complete", "")]

    def test_thinking_and_response(self):
        lines = [
            json.dumps({"message": {"thinking": "Let me think..."}, "done": False}),
            json.dumps({"message": {"content": "Answer"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            results = _event_pairs(_stream())
            assert ("thinking", "Let me think...") in results
            assert ("content", "Answer") in results
            assert results[-1] == ("complete", "")

    def test_http_error(self):
        resp = MagicMock()
        resp.status_code = 500

        with patch("requests.post", return_value=resp):
            results = _event_pairs(_stream())
            assert results == [("error", "HTTP 500")]

    def test_connection_error(self):
        import requests as req
        with patch("requests.post", side_effect=req.exceptions.ConnectionError):
            results = _event_pairs(_stream())
            assert results == [("error", "无法连接到 Ollama")]

    def test_timeout(self):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.Timeout):
            results = _event_pairs(_stream())
            assert results == [("error", "响应超时")]

    def test_invalid_json_is_protocol_error(self):
        lines = [
            "not valid json",
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            results = list(_stream())
            assert len(results) == 1
            assert results[0].kind == EventKind.ERROR
            assert results[0].error_code == ErrorCode.PROTOCOL
            resp.close.assert_called_once()

    def test_multiline_content(self):
        lines = [
            json.dumps({"message": {"content": "line1\n"}, "done": False}),
            json.dumps({"message": {"content": "line2"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            results = _event_pairs(_stream())
            assert ("content", "line1\n") in results
            assert ("content", "line2") in results

    @pytest.mark.parametrize("data", [[], None, {"message": None}, {"message": {"content": 7}},
                                    {"message": {}, "done": "false"}, {"error": []}, {"unexpected": "field"}])
    def test_malformed_response_is_protocol_error(self, data):
        resp = _fake_stream_response([json.dumps(data)])
        with patch("requests.post", return_value=resp):
            results = list(_stream())
        assert len(results) == 1
        assert results[0].error_code == ErrorCode.PROTOCOL
        resp.close.assert_called_once()

    def test_empty_stream_is_not_success(self):
        resp = _fake_stream_response(["", ""])
        with patch("requests.post", return_value=resp):
            results = list(_stream())
        assert len(results) == 1
        assert results[0].error_code == ErrorCode.PROTOCOL

    def test_stream_creation_defers_image_io(self, tmp_path):
        stream = ChatClient().chat_stream([Message("user", "image", images=[str(tmp_path / "missing.png")])])
        with patch("requests.post") as post:
            results = list(stream)
        post.assert_not_called()
        assert len(results) == 1
        assert results[0].error_code == ErrorCode.IMAGE

    def test_connect_timeout_is_classified_as_timeout(self):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.ConnectTimeout):
            results = list(_stream())
        assert results[0].error_code == ErrorCode.TIMEOUT

    def test_cleanup_failure_does_not_replace_terminal_event(self):
        resp = _fake_stream_response([json.dumps({"message": {"content": "ok"}, "done": True})])
        resp.close.side_effect = OSError("cleanup failed")
        with patch("requests.post", return_value=resp):
            results = list(_stream())
        assert [event.kind for event in results] == [EventKind.CONTENT, EventKind.COMPLETE]

    def test_wrapped_stream_read_timeout_is_not_connection_failure(self):
        import requests
        from urllib3.exceptions import ReadTimeoutError
        resp = _fake_stream_response([])
        resp.iter_lines.side_effect = requests.exceptions.ConnectionError(
            ReadTimeoutError(None, "/api/chat", "Read timed out"),
        )
        with patch("requests.post", return_value=resp):
            results = list(_stream())
        assert len(results) == 1
        assert results[0].error_code == ErrorCode.TIMEOUT
        resp.close.assert_called_once()

    def test_unexpected_transport_failure_has_stable_error_code(self):
        resp = _fake_stream_response([])
        resp.iter_lines.side_effect = RuntimeError("unexpected failure")
        with patch("requests.post", return_value=resp):
            results = list(_stream())
        assert len(results) == 1
        assert results[0].error_code == ErrorCode.INTERNAL
        resp.close.assert_called_once()


class TestNonStreamingChat:
    def test_missing_image_returns_error(self, tmp_path):
        with patch("requests.post") as post:
            result = ChatClient().chat([Message("user", "image", images=[str(tmp_path / "missing.png")])])
        post.assert_not_called()
        assert not result.ok
        assert result.error_code == ErrorCode.IMAGE

    def test_server_error_is_not_success(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"error": "model failed"}
        with patch("requests.post", return_value=resp):
            result = ChatClient().chat([Message("user", "hello")])
        assert not result.ok
        assert result.error_code == ErrorCode.SERVER
        resp.close.assert_called_once()


class TestListModels:
    """模型列表测试"""

    def test_returns_models(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "llama3"}, {"name": "qwen3"}]}

        with patch("requests.get", return_value=resp):
            models = list_models()
            assert models == ["llama3", "qwen3"]

    def test_fallback_on_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.ConnectionError):
            models = list_models()
            assert models == []  # no models available


class TestBuildOllamaMessages:
    """_build_ollama_messages 多模态构建测试"""

    def test_text_only_message(self):
        client = ChatClient()
        msgs = [Message(role="user", content="你好")]
        built = client._build_ollama_messages(msgs)
        assert len(built) == 1
        assert built[0] == {"role": "user", "content": "你好"}
        assert "images" not in built[0]

    def test_message_with_images(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(_PNG_BYTES)

        client = ChatClient()
        msgs = [Message(role="user", content="这是什么", images=[str(img)])]
        built = client._build_ollama_messages(msgs)
        assert len(built) == 1
        assert built[0]["role"] == "user"
        assert built[0]["content"] == "这是什么"
        assert built[0]["images"] == [base64.b64encode(_PNG_BYTES).decode("ascii")]

    def test_system_prompt_prepended_and_unchanged(self):
        client = ChatClient()
        msgs = [Message(role="user", content="hi")]
        built = client._build_ollama_messages(msgs, system_prompt="SYSTEM")
        assert built[0] == {"role": "system", "content": "SYSTEM"}
        assert built[1] == {"role": "user", "content": "hi"}
