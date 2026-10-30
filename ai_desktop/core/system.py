"""Model management, history export, diagnostics and update checks for the native shell.

Nothing here sends conversation content anywhere. The only non-Ollama request is the optional
update check, which reads the public GitHub Releases feed and uploads nothing.
"""
from __future__ import annotations

import json
import os
import platform
import re
import time
from pathlib import Path
from typing import Any

import requests

from ai_desktop import config
from ai_desktop.core import protocol
from ai_desktop.core.conversations import collapse_versions
from ai_desktop.utils import storage
from ai_desktop.version import __version__

RELEASES_URL = "https://api.github.com/repos/liamamilin/aide/releases/latest"
_FORMATS = ("markdown", "json")


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


def version_tuple(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(p) for p in parts[:3]) if parts else ()


class SystemService:
    def __init__(self, emit) -> None:
        self._emit = emit

    # ── models ─────────────────────────────────────────────
    def models_detail(self, params: dict[str, Any]) -> dict[str, Any]:
        base = str(params.get("base_url") or config.OLLAMA_BASE_URL).rstrip("/")
        try:
            tags = requests.get(f"{base}/api/tags", timeout=5).json().get("models", [])
        except Exception:
            return {"reachable": False, "models": []}
        rows = []
        for item in tags:
            name = item.get("name")
            if not name:
                continue
            try:
                shown = requests.post(f"{base}/api/show", json={"model": name}, timeout=5).json()
            except Exception:
                shown = {}
            caps = shown.get("capabilities") if isinstance(shown, dict) else None
            caps = caps if isinstance(caps, list) else None
            rows.append({
                "name": name, "size": int(item.get("size") or 0),
                "vision": None if caps is None else "vision" in caps,
                "tools": None if caps is None else "tools" in caps,
                "thinking": None if caps is None else "thinking" in caps,
            })
        return {"reachable": True, "models": rows}

    def models_pull(self, params: dict[str, Any]) -> dict[str, Any]:
        model = str(params.get("model", "")).strip()
        if not model or re.search(r"\s", model):
            raise _invalid("模型名不能为空，也不能包含空格。")
        base = str(params.get("base_url") or config.OLLAMA_BASE_URL).rstrip("/")
        try:
            with requests.post(f"{base}/api/pull", json={"model": model, "stream": True},
                               stream=True, timeout=(5, 120)) as response:
                if response.status_code != 200:
                    raise _invalid(f"拉取失败（HTTP {response.status_code}）。")
                for line in response.iter_lines():
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("error"):
                        raise _invalid(str(row["error"]))
                    self._emit("models.pull", {"model": model, "status": str(row.get("status", "")),
                                               "completed": int(row.get("completed") or 0),
                                               "total": int(row.get("total") or 0)})
        except requests.RequestException as exc:
            raise _invalid(f"无法连接 Ollama：{exc.__class__.__name__}") from exc
        return {"model": model}

    # ── export ─────────────────────────────────────────────
    def export(self, params: dict[str, Any]) -> dict[str, Any]:
        fmt = params.get("format", "markdown")
        path = params.get("path")
        if fmt not in _FORMATS:
            raise _invalid("格式只能是 markdown 或 json。")
        if not isinstance(path, str) or not path:
            raise _invalid("需要导出路径。")
        ids = params.get("conversation_ids")
        if ids is None:
            ids, cursor = [], None
            while True:
                page, cursor = storage.page_conversations(limit=100, cursor=cursor)
                ids += [row["id"] for row in page]
                if cursor is None:
                    break
        elif not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise _invalid("conversation_ids 需要是整数列表。")
        convos = []
        for convo_id in ids:
            convo = storage.get_conversation(convo_id)
            if convo is None:
                continue
            turns = [{"role": t["role"], "content": t["content"]} for t in collapse_versions(convo.messages)
                     if t["content"]]
            convos.append({"id": convo.id, "title": convo.title, "agent_id": convo.agent_id,
                           "created_at": convo.created_at, "messages": turns})
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            body = json.dumps({"app": "Aide", "version": __version__, "conversations": convos},
                              ensure_ascii=False, indent=2)
        else:
            body = self._markdown(convos)
        target.write_text(body, encoding="utf-8")
        return {"path": str(target), "conversations": len(convos),
                "messages": sum(len(c["messages"]) for c in convos)}

    @staticmethod
    def _markdown(convos: list[dict[str, Any]]) -> str:
        out = ["# Aide 对话导出", ""]
        for convo in convos:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(convo["created_at"] or 0))
            out += [f"## {convo['title'] or '未命名对话'}", f"*{when}*", ""]
            for turn in convo["messages"]:
                who = "我" if turn["role"] == "user" else "Aide"
                out += [f"**{who}**", "", turn["content"], ""]
        return "\n".join(out).rstrip() + "\n"

    # ── diagnostics ────────────────────────────────────────
    def diagnostics(self, params: dict[str, Any]) -> dict[str, Any]:
        """Versions and reachability only: no conversation text, no file names."""
        base = str(params.get("base_url") or config.OLLAMA_BASE_URL).rstrip("/")
        try:
            ollama = requests.get(f"{base}/api/version", timeout=3).json().get("version", "?")
        except Exception:
            ollama = "unreachable"
        lines = [
            f"Aide {__version__}",
            f"macOS {platform.mac_ver()[0] or platform.platform()} ({platform.machine()})",
            f"Python {platform.python_version()}",
            f"Ollama {ollama} at {base}",
            f"Model setting: {storage.get_setting('last_model', '') or '(none)'}",
            f"Data dir: {os.environ.get('AIDE_DATA_DIR') or '(default)'}",
        ]
        return {"text": "\n".join(lines)}

    # ── updates ────────────────────────────────────────────
    def check_update(self, _params: dict[str, Any]) -> dict[str, Any]:
        url = os.environ.get("AIDE_UPDATE_URL", RELEASES_URL)
        try:
            response = requests.get(url, timeout=8, headers={"Accept": "application/vnd.github+json"})
        except requests.RequestException as exc:
            raise _invalid(f"无法检查更新：{exc.__class__.__name__}") from exc
        if response.status_code == 404:
            return {"current": __version__, "latest": None, "update": False}
        if response.status_code != 200:
            raise _invalid(f"检查更新失败（HTTP {response.status_code}）。")
        data = response.json()
        tag = str(data.get("tag_name", ""))
        newer = version_tuple(tag) > version_tuple(__version__)
        asset = next((a.get("browser_download_url") for a in data.get("assets", [])
                      if str(a.get("name", "")).endswith(".dmg")), None)
        return {"current": __version__, "latest": tag.lstrip("v"), "update": newer,
                "page": data.get("html_url"), "download": asset, "notes": str(data.get("body", ""))[:2000]}
