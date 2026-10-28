"""Method handlers exposed to the shell. P0a: liveness and identity only."""
from __future__ import annotations

import os
import platform
import sys
from typing import Any

from ai_desktop.core import protocol
from ai_desktop.version import __version__


def ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True}


def health(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": __version__,
        "protocol": protocol.PROTOCOL_VERSION,
        "python": platform.python_version(),
        # Packaging invariant: the shipped core must never pull in Qt (GPL, lifecycle bugs).
        "qt_loaded": any(name == "PyQt5" or name.startswith("PyQt5.") for name in sys.modules),
        "data_dir": os.environ.get("AIDE_DATA_DIR", ""),
    }


def build_handlers(emit) -> dict:
    """Wire every method. Imported lazily so ``ping``/``health`` stay cheap to test."""
    from ai_desktop.core.conversations import ConversationService
    from ai_desktop.core.preferences import PreferencesService
    from ai_desktop.core.runs import RunService

    runs = RunService(emit)
    chats = ConversationService(runs)
    prefs = PreferencesService(runs)
    return {
        "ping": ping, "health": health,
        "actions.list": runs.list_actions, "run.start": runs.start, "run.cancel": runs.cancel,
        "run.confirm": runs.confirm,
        "conversations.list": chats.list, "conversations.get": chats.get, "conversations.create": chats.create,
        "conversations.delete": chats.delete, "conversations.rename": chats.rename,
        "conversations.set_agent": chats.set_agent, "chat.send": chats.send,
        "actions.manage": prefs.list_actions, "actions.save": prefs.save_action,
        "actions.delete": prefs.delete_action,
        "agents.list": chats.agents, "agents.save": prefs.save_agent, "agents.delete": prefs.delete_agent,
        "settings.get": prefs.get, "settings.set": prefs.set, "settings.test_connection": prefs.test_connection,
        "models.list": chats.models, "models.select": chats.select_model,
    }
