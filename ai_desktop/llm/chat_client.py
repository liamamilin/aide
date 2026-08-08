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


class _ThinkTagFilter:
    """从流式正文中移除可能跨 chunk 的 <think> 内容。"""

    def __init__(self):
        self._buffer = ""
        self._inside_think = False

    def feed(self, text: str) -> str:
        self._buffer += text
        output: list[str] = []

        while self._buffer:
            tag = "</think>" if self._inside_think else "<think>"
            lower_buffer = self._buffer.lower()
            tag_index = lower_buffer.find(tag)
            if tag_index >= 0:
                if not self._inside_think:
                    output.append(self._buffer[:tag_index])
                self._buffer = self._buffer[tag_index + len(tag):]
                self._inside_think = not self._inside_think
                continue

            partial_length = self._partial_tag_length(lower_buffer, tag)
            safe_length = len(self._buffer) - partial_length
            if not self._inside_think:
                output.append(self._buffer[:safe_length])
            self._buffer = self._buffer[safe_length:]
            break

        return "".join(output)

    def finish(self) -> str:
        remaining = "" if self._inside_think else self._buffer
        self._buffer = ""
        return remaining

    @staticmethod
    def _partial_tag_length(text: str, tag: str) -> int:
        max_length = min(len(text), len(tag) - 1)
        for length in range(max_length, 0, -1):
            if text.endswith(tag[:length]):
                return length
        return 0


def _without_think_tags(text: str) -> str:
    tag_filter = _ThinkTagFilter()
    return tag_filter.feed(text) + tag_filter.finish()


def list_models(base_url: str = "") -> List[str]:
    """列出本地可用的 Ollama 模型名称"""
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return [m["name"] for m in models]
        logger.warning("list_models: HTTP %d from %s", resp.status_code, url)
    except Exception as e:
        logger.warning("list_models: failed to reach %s: %s", url, e)
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
                if not config.OLLAMA_THINK:
                    text = _without_think_tags(text)
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
        think_enabled = config.OLLAMA_THINK
        tag_filter = _ThinkTagFilter() if not think_enabled else None
        try:
            resp = requests.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": self._messages,
                    "stream": True,
                    "think": think_enabled,
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
                thinking = msg.get("thinking", "") or msg.get("reasoning_content", "")
                content = msg.get("content", "")
                if think_enabled and thinking:
                    yield ("thinking", thinking)
                if content:
                    response = content if tag_filter is None else tag_filter.feed(content)
                    if response:
                        yield ("response", response)
                if data.get("done"):
                    if tag_filter is not None:
                        remaining = tag_filter.finish()
                        if remaining:
                            yield ("response", remaining)
                    return

            if tag_filter is not None:
                remaining = tag_filter.finish()
                if remaining:
                    yield ("response", remaining)

        except requests.exceptions.ConnectionError:
            yield ("error", "无法连接到 Ollama")
        except requests.exceptions.Timeout:
            yield ("error", "响应超时")
        except Exception as e:
            logger.exception("ChatStream error")
            yield ("error", str(e))
