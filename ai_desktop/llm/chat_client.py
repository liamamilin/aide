"""
Immutable Ollama request preparation. Chat execution belongs to RunWorker / QtChatTransport.
"""
import json
import logging
import time
import uuid
from collections.abc import Iterable
from typing import List

import requests
from urllib3.exceptions import TimeoutError as HTTPTimeoutError

from ai_desktop import config
from ai_desktop.llm.events import ErrorCode, RequestContext, RequestMessage
from ai_desktop.llm.model_options import global_options
from ai_desktop.llm.ollama_protocol import StreamProtocolError
from ai_desktop.llm.thinking import ThinkMode, ThinkSetting, normalize_think, resolve_think
from ai_desktop.utils import images as image_utils
from ai_desktop.utils.storage import Message

logger = logging.getLogger(__name__)
_USE_GLOBAL = object()


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


class ImagePreparationError(Exception):
    """An attachment could not be read before submitting the request."""


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


def _payload(request: RequestContext, stream: bool) -> dict:
    return {
        "model": request.model,
        "messages": ChatClient._build_ollama_messages(request.messages, request.system_prompt),
        "stream": stream,
        "think": request.think,
        "keep_alive": request.keep_alive,
        "options": dict(request.options),
    }


class ChatClient:
    """Build request snapshots and prepare messages without sending chat HTTP."""

    def __init__(self, base_url: str = "", model: str = "", timeout: int = 0):
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT

    def create_request(self, messages: Iterable[Message], system_prompt: str = "", *,
                       conversation_id: int = 0, agent_id: str = "",
                       think: bool | str | None = _USE_GLOBAL,
                       think_setting: ThinkSetting | None = None, think_source: str = "",
                       origin: str = "chat", action_id: str | None = None,
                       options: dict[str, int | float] | None = None) -> RequestContext:
        """Snapshot on submission; image I/O stays in the worker."""
        if origin not in {"chat", "action"}:
            raise ValueError("Unknown request origin")
        if action_id is not None and origin != "action":
            raise ValueError("Action ID requires action origin")
        if think is _USE_GLOBAL:
            think_setting = normalize_think(config.OLLAMA_THINK)
            think, _ = resolve_think(think_setting, None)
            think_source = "全局设置"
        elif think is not None and type(think) is not bool and not isinstance(think, str):
            raise ValueError("think wire value must be bool, string or null")
        if think_setting is None:
            think_setting = (ThinkSetting(ThinkMode.NAMED, think) if isinstance(think, str) else
                             normalize_think(think))
        resolved_options = global_options()
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
            think=think,
            keep_alive=config.OLLAMA_KEEP_ALIVE,
            options=tuple(resolved_options.items()),
            created_at=time.time(),
            run_id=uuid.uuid4().hex,
            step_id=uuid.uuid4().hex,
            origin=origin,
            action_id=action_id,
            think_setting=think_setting, think_source=think_source or "本次请求",
        )

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
