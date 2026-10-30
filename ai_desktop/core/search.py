"""Opt-in web search for the native shell.

Off by default and per conversation. When it is on, the model may send a search query to the configured
provider (Parallel or Exa) using the API key the person saved in the macOS Keychain. Nothing else leaves
the Mac: the key is never returned to the shell, and results are untrusted data for the model.
"""
from __future__ import annotations

from typing import Any

from ai_desktop import config
from ai_desktop.core import protocol
from ai_desktop.services.search_credentials import PROVIDERS, CredentialError, SearchCredentials
from ai_desktop.services.web_search import SearchSettings
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.utils import storage


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


def conversation_flag(conversation_id: int) -> bool:
    return storage.get_setting(f"search_conv_{conversation_id}", "0") == "1"


class SearchService:
    def __init__(self, credentials: SearchCredentials | None = None) -> None:
        self.credentials = credentials or SearchCredentials()
        self._settings = SettingsManager()

    def has_key(self) -> bool:
        try:
            return bool(self.credentials.get(config.SEARCH_PROVIDER, interactive=False))
        except CredentialError:
            return False

    def settings(self) -> SearchSettings:
        return SearchSettings.from_config()

    # ── methods ────────────────────────────────────────────
    def status(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"provider": config.SEARCH_PROVIDER, "providers": sorted(PROVIDERS), "has_key": self.has_key()}

    def save_key(self, params: dict[str, Any]) -> dict[str, Any]:
        provider, key = params.get("provider"), params.get("key")
        if provider not in PROVIDERS:
            raise _invalid("不支持的搜索服务。")
        if not isinstance(key, str):
            raise _invalid("需要填写密钥。")
        try:
            self.credentials.set(provider, key.strip())
        except CredentialError as exc:
            raise _invalid(str(exc)) from exc
        self._settings.apply({"search_provider": provider})
        return self.status({})

    def delete_key(self, params: dict[str, Any]) -> dict[str, Any]:
        provider = params.get("provider", config.SEARCH_PROVIDER)
        if provider not in PROVIDERS:
            raise _invalid("不支持的搜索服务。")
        try:
            self.credentials.delete(provider)
        except CredentialError as exc:
            raise _invalid(str(exc)) from exc
        return self.status({})

    def set_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        convo_id, enabled = params.get("conversation_id"), params.get("enabled")
        if not isinstance(convo_id, int) or isinstance(convo_id, bool) or not isinstance(enabled, bool):
            raise _invalid("需要对话编号和开关状态。")
        if storage.get_conversation(convo_id) is None:
            raise _invalid("对话不存在或已被删除。")
        if enabled and not self.has_key():
            raise _invalid("请先在设置的「联网搜索」页保存搜索服务的 API 密钥。")
        storage.save_setting(f"search_conv_{convo_id}", "1" if enabled else "0")
        return {"conversation_id": convo_id, "enabled": enabled}
