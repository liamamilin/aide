"""Fresh model-capability check before a tool run (Qt-free twin of ``llm/task_checks``).

Three loopback requests: service version, installed models, and the model's capabilities. Any failure is
a ``ValueError`` with a message the person can act on; callers then run the message tool-free and say why.
"""
from __future__ import annotations

import requests

from ai_desktop.services.task_admission import (
    TaskAdmission,
    TaskModelSettings,
    local_service_url,
    validate_discovery,
)

_TIMEOUT = 5
_MAX_BYTES = 1024 * 1024


def _json(response: requests.Response) -> dict:
    if response.status_code != 200 or len(response.content) > _MAX_BYTES:
        raise ValueError("无法验证本机 Ollama，请检查服务连接。")
    try:
        value = response.json()
    except ValueError:
        raise ValueError("模型准入响应无效，未启用工具。") from None
    if not isinstance(value, dict):
        raise ValueError("模型准入响应无效，未启用工具。")
    return value


def check_admission(base_url: str, model: str, settings: TaskModelSettings) -> TaskAdmission:
    url = local_service_url(base_url)
    try:
        version = _json(requests.get(f"{url}/api/version", timeout=_TIMEOUT, allow_redirects=False))
        tags = _json(requests.get(f"{url}/api/tags", timeout=_TIMEOUT, allow_redirects=False))
        show = _json(requests.post(f"{url}/api/show", json={"model": model}, timeout=_TIMEOUT,
                                   allow_redirects=False))
    except requests.RequestException:
        raise ValueError("无法验证本机 Ollama，请检查服务连接。") from None
    return validate_discovery(url, version, tags, show, model=model, settings=settings)
