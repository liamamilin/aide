"""Settings and custom-agent methods for the native shell.

Only the settings a person is expected to touch are exposed: the Ollama address and thinking mode up
front, sampling and limits as "advanced". Pet, search and tool toggles stay with the Qt shell until
the native shell has the matching features.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ai_desktop import config
from ai_desktop.core import protocol
from ai_desktop.core.runs import RunService
from ai_desktop.llm.chat_client import list_models
from ai_desktop.settings_manager import SettingsManager

# key -> (low, high, integer?) for the advanced numeric limits
_RANGES: dict[str, tuple[float, float, bool]] = {
    "timeout": (5, 3600, True),
    "num_ctx": (512, 1_048_576, True),
    "num_predict": (-2, 1_048_576, True),
    "temperature": (0.0, 2.0, False),
    "top_p": (0.0, 1.0, False),
    "top_k": (0, 1000, True),
    "repeat_penalty": (0.0, 3.0, False),
    "max_rounds": (1, 200, True),
}
_EXPOSED = ("base_url", "think", *_RANGES)
_NAME_CHARS = 40
_PROMPT_CHARS = 8000


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


class PreferencesService:
    def __init__(self, runs: RunService) -> None:
        self._runs = runs
        self._settings = SettingsManager()

    # ── settings ───────────────────────────────────────────
    def get(self, _params: dict[str, Any]) -> dict[str, Any]:
        current = self._settings.current()
        values = {key: current[key] for key in _EXPOSED if key != "think"}
        values["think"] = current["think"].record()
        return {"settings": values, "hotkey": config.HOTKEY}

    def set(self, params: dict[str, Any]) -> dict[str, Any]:
        incoming = params.get("settings")
        if not isinstance(incoming, dict) or not incoming:
            raise _invalid("settings required")
        accepted: dict[str, Any] = {}
        for key, value in incoming.items():
            if key not in _EXPOSED:
                raise _invalid(f"未知的设置项：{key}")
            accepted[key] = self._validate(key, value)
        changed = self._settings.apply(accepted)
        missed = [key for key, value in accepted.items() if key not in changed and key != "think"
                  and self._settings.current()[key] != value]
        if missed:
            raise _invalid(f"设置未能保存：{', '.join(missed)}")
        return {"changed": changed, **self.get({})}

    def test_connection(self, params: dict[str, Any]) -> dict[str, Any]:
        url = params.get("base_url") or config.OLLAMA_BASE_URL
        if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
            raise _invalid("服务地址需要以 http:// 或 https:// 开头。")
        models = list_models(url)
        return {"ok": bool(models), "model_count": len(models)}

    @staticmethod
    def _validate(key: str, value: Any) -> Any:
        if key == "base_url":
            if not isinstance(value, str) or not re.match(r"^https?://\S+$", value.strip()):
                raise _invalid("服务地址需要以 http:// 或 https:// 开头。")
            return value.strip().rstrip("/")
        if key == "think":
            if not isinstance(value, dict) or value.get("mode") not in ("model_default", "on", "off"):
                raise _invalid("思考选项只能是 模型默认 / 开启 / 关闭。")
            return {"mode": value["mode"]}
        low, high, integral = _RANGES[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _invalid(f"{key} 需要是数字。")
        if integral and value != int(value):
            raise _invalid(f"{key} 需要是整数。")
        if not low <= value <= high:
            raise _invalid(f"{key} 需要在 {low:g} 到 {high:g} 之间。")
        return int(value) if integral else float(value)

    # ── custom agents ──────────────────────────────────────
    def save_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        manager = self._runs.agent_manager
        name = str(params.get("name", "")).strip()
        prompt = str(params.get("system_prompt", "")).strip()
        icon = str(params.get("icon", "")).strip() or "🤖"
        if not name or len(name) > _NAME_CHARS:
            raise _invalid(f"名称不能为空，且不超过 {_NAME_CHARS} 个字。")
        if not prompt or len(prompt) > _PROMPT_CHARS:
            raise _invalid(f"提示词不能为空，且不超过 {_PROMPT_CHARS} 个字。")
        agent_id = params.get("id")
        customs = [{"id": a.id, "name": a.name, "icon": a.icon, "system_prompt": a.system_prompt}
                   for a in manager.custom_agents]
        if agent_id is None:
            agent_id = f"custom_{uuid.uuid4().hex[:8]}"
            customs.append({"id": agent_id, "name": name, "icon": icon, "system_prompt": prompt})
        else:
            target = next((c for c in customs if c["id"] == agent_id), None)
            if target is None:
                raise _invalid("只能修改自定义助手。")
            target.update(name=name, icon=icon, system_prompt=prompt)
        manager.save_custom(customs)
        return {"id": agent_id}

    def delete_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        manager = self._runs.agent_manager
        agent_id = params.get("id")
        customs = [{"id": a.id, "name": a.name, "icon": a.icon, "system_prompt": a.system_prompt}
                   for a in manager.custom_agents]
        remaining = [c for c in customs if c["id"] != agent_id]
        if len(remaining) == len(customs):
            raise _invalid("只能删除自定义助手。")
        manager.save_custom(remaining)
        return {}
