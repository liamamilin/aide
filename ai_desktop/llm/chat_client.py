"""
LLM 聊天客户端（Ollama /api/chat）
"""
import json
import logging
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import List

import requests
from urllib3.exceptions import TimeoutError as HTTPTimeoutError

from ai_desktop import config
from ai_desktop.llm.events import ErrorCode, EventKind, RequestContext, RequestMessage, StreamEvent
from ai_desktop.utils import images as image_utils
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
        logger.warning("list_models: HTTP %d from %s", resp.status_code, url)
    except Exception as e:
        logger.warning("list_models: failed to reach %s: %s", url, e)
    return []  # fallback: no models available


@dataclass
class ChatResponse:
    text: str
    ok: bool
    error: str = ""
    error_code: ErrorCode | None = None


class ImagePreparationError(Exception):
    """An attachment could not be read before submitting the request."""


class StreamProtocolError(Exception):
    """The server response is malformed or ends without a completion event."""


def _exception_error(exc: Exception) -> tuple[ErrorCode, str]:
    if isinstance(exc, ImagePreparationError):
        return ErrorCode.IMAGE, "图片读取失败，请重新添加图片。"
    # requests wraps streaming read timeouts in ConnectionError.
    wrapped_timeout = isinstance(exc, requests.exceptions.ConnectionError) and any(
        isinstance(reason, HTTPTimeoutError) for reason in exc.args
    )
    if isinstance(exc, requests.exceptions.Timeout) or wrapped_timeout:
        return ErrorCode.TIMEOUT, "响应超时"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ErrorCode.CONNECTION, "无法连接到 Ollama"
    if isinstance(exc, (StreamProtocolError, json.JSONDecodeError, UnicodeError)):
        return ErrorCode.PROTOCOL, "Ollama 响应格式错误或连接提前结束，请重试。"
    return ErrorCode.INTERNAL, "请求处理失败，请重试。"


def _response_message(data: object) -> tuple[dict, str]:
    if not isinstance(data, dict):
        raise StreamProtocolError("Response must be an object")
    if "error" in data:
        error = data["error"]
        if not isinstance(error, str) or not error:
            raise StreamProtocolError("Invalid server error")
        return {}, error
    message = data.get("message", {})
    if not isinstance(message, dict):
        raise StreamProtocolError("Invalid message")
    for key in ("content", "thinking"):
        if not isinstance(message.get(key, ""), str):
            raise StreamProtocolError(f"Invalid {key}")
    if not isinstance(data.get("done", False), bool):
        raise StreamProtocolError("Invalid completion flag")
    if "message" not in data and data.get("done") is not True:
        raise StreamProtocolError("Missing message")
    return message, ""


def _payload(request: RequestContext, stream: bool) -> dict:
    return {
        "model": request.model,
        "messages": ChatClient._build_ollama_messages(request.messages, request.system_prompt),
        "stream": stream,
        "think": request.think,
        "keep_alive": request.keep_alive,
        "options": dict(request.options),
    }


def _close_response(response) -> None:
    if response is not None:
        try:
            response.close()
        except Exception:
            logger.warning("Failed to release HTTP response", exc_info=True)


class ChatClient:
    """Ollama Chat API 客户端"""

    def __init__(self, base_url: str = "", model: str = "", timeout: int = 0):
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT

    def create_request(self, messages: Iterable[Message], system_prompt: str = "", *,
                       conversation_id: int = 0, agent_id: str = "",
                       think: bool | None = None,
                       options: dict[str, int | float] | None = None) -> RequestContext:
        """Snapshot on submission; image I/O stays in the worker."""
        resolved_options: dict[str, int | float] = {
            "num_predict": config.OLLAMA_NUM_PREDICT,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "temperature": config.OLLAMA_TEMPERATURE,
            "top_p": config.OLLAMA_TOP_P,
            "top_k": config.OLLAMA_TOP_K,
            "repeat_penalty": config.OLLAMA_REPEAT_PENALTY,
        }
        resolved_options.update(options or {})
        return RequestContext(
            request_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            agent_id=agent_id,
            system_prompt=system_prompt,
            messages=tuple(RequestMessage(m.role, m.content, m.id, tuple(m.images)) for m in messages),
            base_url=self.base_url,
            model=self.model,
            timeout=self.timeout,
            think=config.OLLAMA_THINK if think is None else think,
            keep_alive=config.OLLAMA_KEEP_ALIVE,
            options=tuple(resolved_options.items()),
            created_at=time.time(),
        )

    def chat(self, messages: list[Message], system_prompt: str = "") -> ChatResponse:
        """发送多轮对话，返回助手的回复"""
        request = self.create_request(messages, system_prompt)
        resp = None
        try:
            resp = requests.post(
                f"{request.base_url}/api/chat",
                json=_payload(request, stream=False),
                timeout=request.timeout,
            )
            if resp.status_code == 200:
                message, error = _response_message(resp.json())
                if error:
                    return ChatResponse(text="", ok=False, error=error, error_code=ErrorCode.SERVER)
                return ChatResponse(text=message.get("content", ""), ok=True)
            else:
                return ChatResponse(text="", ok=False, error=f"HTTP {resp.status_code}", error_code=ErrorCode.HTTP)
        except Exception as e:
            code, error = _exception_error(e)
            logger.warning("Chat failed: %s", code.value, exc_info=code == ErrorCode.INTERNAL)
            return ChatResponse(text="", ok=False, error=error, error_code=code)
        finally:
            _close_response(resp)

    def chat_stream(self, messages: list[Message], system_prompt: str = "") -> "ChatStream":
        """Return typed events, including exactly one terminal event."""
        return ChatStream(self.create_request(messages, system_prompt))

    @staticmethod
    def _build_ollama_messages(messages: Iterable[Message | RequestMessage], system_prompt: str = "") -> list[dict]:
        ollama_msgs: list[dict] = []
        if system_prompt:
            ollama_msgs.append({"role": "system", "content": system_prompt})
        for m in messages:
            msg: dict = {"role": m.role, "content": m.content}
            images = getattr(m, "images", None)
            if images:
                try:
                    msg["images"] = [image_utils.encode_image_base64(p) for p in images]
                except OSError as exc:
                    raise ImagePreparationError from exc
            ollama_msgs.append(msg)
        return ollama_msgs


class ChatStream:
    """Ollama streaming chat 迭代器"""

    def __init__(self, request: RequestContext):
        self.request = request

    def __iter__(self) -> Iterator[StreamEvent]:
        request = self.request
        resp = None
        try:
            resp = requests.post(
                f"{request.base_url}/api/chat",
                json=_payload(request, stream=True),
                timeout=request.timeout,
                stream=True,
            )
            if resp.status_code != 200:
                yield StreamEvent(request.request_id, EventKind.ERROR, f"HTTP {resp.status_code}", ErrorCode.HTTP)
                return

            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                data = json.loads(line)
                message, error = _response_message(data)
                if error:
                    yield StreamEvent(request.request_id, EventKind.ERROR, error, ErrorCode.SERVER)
                    return
                thinking = message.get("thinking", "")
                content = message.get("content", "")
                if thinking:
                    yield StreamEvent(request.request_id, EventKind.THINKING, thinking)
                if content:
                    yield StreamEvent(request.request_id, EventKind.CONTENT, content)
                if data.get("done"):
                    yield StreamEvent(request.request_id, EventKind.COMPLETE)
                    return
            raise StreamProtocolError("Missing completion event")
        except Exception as e:
            code, error = _exception_error(e)
            logger.warning("Chat stream %s failed: %s", request.request_id, code.value,
                           exc_info=code == ErrorCode.INTERNAL)
            yield StreamEvent(request.request_id, EventKind.ERROR, error, code)
        finally:
            _close_response(resp)
