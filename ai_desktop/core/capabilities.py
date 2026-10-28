"""Model thinking capability for the headless core (same cache and parser as the Qt app).

Without a known capability the "off"/"on" thinking setting cannot be sent and the request falls back
to the model default, which for reasoning models means thinking is ON (seconds of extra latency).
"""
from __future__ import annotations

import logging

import requests

from ai_desktop import config
from ai_desktop.llm.thinking import ThinkingCapability, cache_thinking, load_cached_thinking, parse_thinking

logger = logging.getLogger(__name__)


def _digest(base_url: str, model: str) -> str:
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=5)
        for item in response.json().get("models", []):
            if item.get("name") == model:
                digest = item.get("digest", "")
                return digest.strip() if isinstance(digest, str) else ""
    except Exception:
        logger.debug("tags lookup failed", exc_info=True)
    return ""


def thinking_capability(model: str, base_url: str = "") -> ThinkingCapability:
    base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    version = _digest(base_url, model)
    cached = load_cached_thinking(base_url, model, version)
    if cached.known:
        return cached
    try:
        response = requests.post(f"{base_url}/api/show", json={"model": model}, timeout=5)
        data = response.json() if response.status_code == 200 else {}
    except Exception:
        logger.debug("show probe failed", exc_info=True)
        return ThinkingCapability()
    capability = parse_thinking(data) if isinstance(data, dict) else ThinkingCapability()
    if capability.known:
        cache_thinking(base_url, model, version, capability)
    return capability


def vision_capable(model: str, base_url: str = "") -> bool | None:
    """True/False from the model's declared capabilities; None when Ollama cannot say."""
    base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    try:
        response = requests.post(f"{base_url}/api/show", json={"model": model}, timeout=5)
        data = response.json() if response.status_code == 200 else {}
    except Exception:
        logger.debug("vision probe failed", exc_info=True)
        return None
    capabilities = data.get("capabilities") if isinstance(data, dict) else None
    return "vision" in capabilities if isinstance(capabilities, list) else None
