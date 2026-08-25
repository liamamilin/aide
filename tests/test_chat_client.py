"""LLM chat client tests — mock HTTP responses"""
import base64
import json
from unittest.mock import MagicMock, patch

from ai_desktop.llm.chat_client import ChatClient, ChatStream, list_models
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


class TestChatStream:
    """chat_stream 迭代器测试"""

    def test_response_tokens(self):
        lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert results == [("response", "Hello"), ("response", " world")]

    def test_thinking_and_response(self):
        lines = [
            json.dumps({"message": {"thinking": "Let me think..."}, "done": False}),
            json.dumps({"message": {"content": "Answer"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert ("thinking", "Let me think...") in results
            assert ("response", "Answer") in results

    def test_http_error(self):
        resp = MagicMock()
        resp.status_code = 500

        with patch("requests.post", return_value=resp):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert results == [("error", "HTTP 500")]

    def test_connection_error(self):
        import requests as req
        with patch("requests.post", side_effect=req.exceptions.ConnectionError):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert results == [("error", "无法连接到 Ollama")]

    def test_timeout(self):
        import requests
        with patch("requests.post", side_effect=requests.exceptions.Timeout):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert results == [("error", "响应超时")]

    def test_invalid_json_skipped(self):
        lines = [
            "not valid json",
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert len(results) == 1
            assert results[0] == ("response", "ok")

    def test_multiline_content(self):
        lines = [
            json.dumps({"message": {"content": "line1\n"}, "done": False}),
            json.dumps({"message": {"content": "line2"}, "done": True}),
        ]
        resp = _fake_stream_response(lines)

        with patch("requests.post", return_value=resp):
            stream = ChatStream("http://localhost", "model", [], 10)
            results = list(stream)
            assert ("response", "line1\n") in results
            assert ("response", "line2") in results


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
