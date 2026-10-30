"""Opt-in Bash for the native shell.

Off by default and per conversation, and only inside one folder the person chose. Reading commands from a
small proven-safe list may run on their own (``readonly_auto``); anything else shows the exact command and
the folder and waits for approval, exactly like the Qt app. Nothing here ever raises the limits of
``services/bash_policy`` or ``services/bash_executor``.
"""
from __future__ import annotations

import os
from typing import Any

from ai_desktop.core import protocol
from ai_desktop.services.execution_context import (
    BashPolicy,
    ExecutionSnapshot,
    current_execution_snapshot,
    load_execution_preferences,
    save_execution_preferences,
)
from ai_desktop.utils import storage

_HOME = os.path.expanduser("~")


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


def conversation_flag(conversation_id: int) -> bool:
    return storage.get_setting(f"bash_conv_{conversation_id}", "0") == "1"


class ShellService:
    def snapshot(self) -> ExecutionSnapshot | None:
        """The saved folder as a fresh snapshot, or None when none is set or it is gone."""
        try:
            return current_execution_snapshot()
        except (ValueError, OSError, RuntimeError):
            return None

    def status(self, _params: dict[str, Any]) -> dict[str, Any]:
        prefs = load_execution_preferences()
        usable = self.snapshot() is not None
        return {"workspace": prefs["execution_workspace"], "usable": usable, "policy": prefs["bash_policy"]}

    def set_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        path, policy = params.get("path"), params.get("policy", BashPolicy.READONLY_AUTO.value)
        if not isinstance(path, str) or not path.strip():
            raise _invalid("请选择一个文件夹。")
        if policy not in {p.value for p in BashPolicy}:
            raise _invalid("命令策略无效。")
        expanded = os.path.realpath(os.path.expanduser(path.strip()))
        if expanded in {"/", _HOME}:
            raise _invalid("为了安全，不能把根目录或整个个人文件夹作为命令的工作文件夹，请选一个具体的项目文件夹。")
        try:
            prefs = load_execution_preferences()
            save_execution_preferences({**prefs, "execution_workspace": expanded, "bash_policy": policy})
        except (ValueError, OSError) as exc:
            raise _invalid(str(exc) or "无法使用这个文件夹。") from exc
        return self.status({})

    def clear_workspace(self, _params: dict[str, Any]) -> dict[str, Any]:
        save_execution_preferences({"execution_workspace": ""})
        return self.status({})

    def set_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        convo_id, enabled = params.get("conversation_id"), params.get("enabled")
        if not isinstance(convo_id, int) or isinstance(convo_id, bool) or not isinstance(enabled, bool):
            raise _invalid("需要对话编号和开关状态。")
        if storage.get_conversation(convo_id) is None:
            raise _invalid("对话不存在或已被删除。")
        if enabled and self.snapshot() is None:
            raise _invalid("请先在设置的「命令行」页选择一个工作文件夹。")
        storage.save_setting(f"bash_conv_{convo_id}", "1" if enabled else "0")
        return {"conversation_id": convo_id, "enabled": enabled}
