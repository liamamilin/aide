"""
LLM 聊天客户端（Ollama /api/chat）
"""
import json
import logging
from dataclasses import dataclass
from typing import List

import requests

from ai_desktop import config
from ai_desktop.utils.storage import Message

logger = logging.getLogger(__name__)


def list_models(base_url: str = "") -> List[str]:
    """列出本地可用的 Ollama 模型名称"""
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return [m["name"] for m in models]
    except Exception:
        logger.debug("Failed to list models", exc_info=True)
    return []  # fallback: no models available


@dataclass
class ChatResponse:
    text: str
    ok: bool
    error: str = ""


class ChatClient:
    """Ollama Chat API 客户端"""

    def __init__(self, base_url: str = "", model: str = "", timeout: int = 0):
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT

    def chat(self, messages: list[Message], system_prompt: str = "") -> ChatResponse:
        """发送多轮对话，返回助手的回复"""
        ollama_msgs = self._build_ollama_messages(messages, system_prompt)

        try:
            payload = {
                "model": self.model,
                "messages": ollama_msgs,
                "stream": False,
                "think": config.OLLAMA_THINK,
                "keep_alive": config.OLLAMA_KEEP_ALIVE,
                "options": {
                    "num_predict": config.OLLAMA_NUM_PREDICT,
                    "num_ctx": config.OLLAMA_NUM_CTX,
                    "temperature": config.OLLAMA_TEMPERATURE,
                    "top_p": config.OLLAMA_TOP_P,
                    "top_k": config.OLLAMA_TOP_K,
                    "repeat_penalty": config.OLLAMA_REPEAT_PENALTY,
                },
            }
            logger.info("Chat: %d messages → %s", len(ollama_msgs), self.model)

            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )

            if resp.status_code == 200:
                data = resp.json()
                text = data.get("message", {}).get("content", "")
                logger.info("Chat returned %d chars", len(text))
                return ChatResponse(text=text, ok=True)
            else:
                msg = f"Chat HTTP {resp.status_code}"
                logger.error(msg)
                return ChatResponse(text="", ok=False, error=msg)

        except requests.exceptions.ConnectionError:
            return ChatResponse(text="", ok=False, error="无法连接到 Ollama")
        except requests.exceptions.Timeout:
            return ChatResponse(text="", ok=False, error="响应超时")
        except Exception as e:
            logger.exception("Chat error")
            return ChatResponse(text="", ok=False, error=str(e))

    def chat_stream(self, messages: list[Message], system_prompt: str = "") -> "ChatStream":
        """返回流式迭代器，每步 yield 一个 token 字符串；结束时 yield None"""
        ollama_msgs = self._build_ollama_messages(messages, system_prompt)
        return ChatStream(self.base_url, self.model, ollama_msgs, self.timeout)

    def _build_ollama_messages(self, messages: list[Message], system_prompt: str = "") -> list[dict]:
        ollama_msgs: list[dict] = []
        if system_prompt:
            ollama_msgs.append({"role": "system", "content": system_prompt})
        for m in messages:
            ollama_msgs.append({"role": m.role, "content": m.content})
        return ollama_msgs


class ChatStream:
    """Ollama streaming chat 迭代器"""

    def __init__(self, base_url: str, model: str, messages: list[dict], timeout: int):
        self._base_url = base_url
        self._model = model
        self._messages = messages
        self._timeout = timeout

    def __iter__(self):
        try:
            resp = requests.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": self._messages,
                    "stream": True,
                    "think": config.OLLAMA_THINK,
                    "keep_alive": config.OLLAMA_KEEP_ALIVE,
                    "options": {
                        "num_predict": config.OLLAMA_NUM_PREDICT,
                        "num_ctx": config.OLLAMA_NUM_CTX,
                        "temperature": config.OLLAMA_TEMPERATURE,
                        "top_p": config.OLLAMA_TOP_P,
                        "top_k": config.OLLAMA_TOP_K,
                        "repeat_penalty": config.OLLAMA_REPEAT_PENALTY,
                    },
                },
                timeout=self._timeout,
                stream=True,
            )
            if resp.status_code != 200:
                yield ("error", f"HTTP {resp.status_code}")
                return

            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message", {})
                thinking = msg.get("thinking", "")
                content = msg.get("content", "")
                if thinking:
                    yield ("thinking", thinking)
                if content:
                    yield ("response", content)
                if data.get("done"):
                    return

        except requests.exceptions.ConnectionError:
            yield ("error", "无法连接到 Ollama")
        except requests.exceptions.Timeout:
            yield ("error", "响应超时")
        except Exception as e:
            logger.exception("ChatStream error")
            yield ("error", str(e))
