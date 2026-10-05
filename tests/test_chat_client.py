"""Request/message preparation and service discovery; no second chat transport."""
import base64
from unittest.mock import MagicMock, patch

from PyQt5.QtGui import QColor, QImage

from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.utils.storage import Message


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
        image = QImage(2, 2, QImage.Format_ARGB32)
        image.fill(QColor("red"))
        assert image.save(str(img), "PNG")
        image_bytes = img.read_bytes()

        client = ChatClient()
        msgs = [Message(role="user", content="这是什么", images=[str(img)])]
        built = client._build_ollama_messages(msgs)
        assert len(built) == 1
        assert built[0]["role"] == "user"
        assert built[0]["content"] == "这是什么"
        assert built[0]["images"] == [base64.b64encode(image_bytes).decode("ascii")]

    def test_system_prompt_prepended_and_unchanged(self):
        client = ChatClient()
        msgs = [Message(role="user", content="hi")]
        built = client._build_ollama_messages(msgs, system_prompt="SYSTEM")
        assert built[0] == {"role": "system", "content": "SYSTEM"}
        assert built[1] == {"role": "user", "content": "hi"}
