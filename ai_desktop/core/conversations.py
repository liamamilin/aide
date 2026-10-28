"""Conversation methods for the Workspace: list / open / create / delete / rename / send.

Reads and writes the same SQLite rows as the Qt app, including answer generations, so history
stays interchangeable between the two shells during the migration. Sends are tool-free in P0c.
"""
from __future__ import annotations

import threading
from typing import Any

from ai_desktop import config
from ai_desktop.core import protocol
from ai_desktop.core.runs import RunService, _Message
from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.llm.events import ChatResult
from ai_desktop.utils import storage

_TITLE_CHARS = 30


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


def collapse_versions(messages: list[storage.Message]) -> list[dict[str, Any]]:
    """One answer per user turn: the active generation if any, else the latest assistant message."""
    turns: list[dict[str, Any]] = []
    pending: list[storage.Message] = []
    last_user: storage.Message | None = None

    def flush() -> None:
        if last_user is None or not pending:
            return
        active = storage.get_active_generation(last_user.id)
        chosen = next((m for m in pending if active and m.id == active.assistant_message_id), pending[-1])
        turns.append({"id": chosen.id, "role": "assistant", "content": chosen.content})

    for message in messages:
        if message.role == "user":
            flush()
            pending = []
            last_user = message
            turns.append({"id": message.id, "role": "user", "content": message.content})
        elif message.role == "assistant" and last_user is not None:
            pending.append(message)
    flush()
    return turns


class ConversationService:
    def __init__(self, runs: RunService) -> None:
        self._runs = runs
        self._busy: set[int] = set()
        self._lock = threading.Lock()

    # ── reading ────────────────────────────────────────────
    def list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = params.get("limit", 100)
        query = params.get("query", "")
        if not isinstance(limit, int) or not isinstance(query, str):
            raise _invalid("limit must be an int and query a string")
        page, _cursor = storage.page_conversations(limit=max(1, min(limit, 100)), query=query)
        return {"conversations": [
            {"id": row["id"], "title": row["title"], "agent_id": row["agent_id"],
             "created_at": row["created_at"], "message_count": row["msg_count"]} for row in page]}

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation = self._require(params)
        return {"id": conversation.id, "title": conversation.title, "agent_id": conversation.agent_id,
                "messages": collapse_versions(conversation.messages)}

    def agents(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"agents": [{"id": a.id, "name": a.name, "icon": a.icon} for a in self._runs.agents]}

    def models(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"models": list_models(), "current": self._current_model()}

    def select_model(self, params: dict[str, Any]) -> dict[str, Any]:
        model = params.get("model")
        if not isinstance(model, str) or not model:
            raise _invalid("model required")
        storage.save_setting("last_model", model)
        return {"current": model}

    # ── writing ────────────────────────────────────────────
    def create(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agent_id") or self._runs.agents[0].id
        seed = params.get("messages", [])
        if not isinstance(agent_id, str) or not isinstance(seed, list):
            raise _invalid("agent_id must be a string and messages a list")
        conversation = storage.create_conversation(agent_id, str(params.get("title", "")))
        first_user = None
        for item in seed:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                raise _invalid("seed messages need role user|assistant and content")
            saved = storage.save_message(conversation.id, item["role"], str(item.get("content", "")))
            if item["role"] == "user":
                first_user = first_user or saved
            elif first_user is not None:
                storage.save_generation(first_user.id, f"seed-{saved.id}", status="succeeded",
                                        answer=saved.content, assistant_message_id=saved.id, active=True)
        if seed and not conversation.title:
            self._auto_title(conversation.id, str(seed[0].get("content", "")))
        return {"id": conversation.id}

    def delete(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation = self._require(params)
        storage.delete_conversation(conversation.id)
        return {}

    def rename(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation = self._require(params)
        try:
            return {"title": storage.update_conversation_title(conversation.id, str(params.get("title", "")))}
        except ValueError as exc:
            raise _invalid(str(exc)) from exc

    def send(self, params: dict[str, Any]) -> dict[str, Any]:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _invalid("text required")
        conversation = self._require(params)
        with self._lock:
            if conversation.id in self._busy:
                raise _invalid("这个对话正在生成回答。")
            self._busy.add(conversation.id)
        try:
            return self._send(conversation, text, params.get("model"))
        except Exception:
            with self._lock:
                self._busy.discard(conversation.id)
            raise

    def _send(self, conversation: storage.Conversation, text: str, model_override: Any) -> dict[str, Any]:
        agent = next((a for a in self._runs.agents if a.id == conversation.agent_id), self._runs.agents[0])
        history = collapse_versions(conversation.messages)
        user_message = storage.save_message(conversation.id, "user", text)
        if not conversation.messages and not conversation.title:
            self._auto_title(conversation.id, text)
        resolved = self._runs.profiles.resolve(
            global_model=model_override if isinstance(model_override, str) and model_override
            else self._current_model(),
            agent_profile_id=agent.profile_id,
        )
        messages = [_Message(m["role"], m["content"]) for m in history] + [_Message("user", text, user_message.id)]
        request = ChatClient(model=resolved.model).create_request(
            messages, agent.system_prompt, conversation_id=conversation.id, agent_id=agent.id,
            think=resolved.think, think_setting=resolved.think_setting, think_source=resolved.think_source,
            options=resolved.options,
        )

        def persist(result: ChatResult) -> None:
            try:
                if result.ok and result.text:
                    assistant = storage.save_message(conversation.id, "assistant", result.text)
                    storage.save_generation(
                        user_message.id, request.request_id, status="succeeded", answer=result.text,
                        assistant_message_id=assistant.id, active=True,
                        config_snapshot={"model": resolved.model, "run_id": request.run_id})
            finally:
                with self._lock:
                    self._busy.discard(conversation.id)

        self._runs.launch(request, on_finished=persist)
        return {"run_id": request.run_id, "conversation_id": conversation.id, "user_message_id": user_message.id,
                "model": resolved.model}

    # ── helpers ────────────────────────────────────────────
    def _require(self, params: dict[str, Any]) -> storage.Conversation:
        convo_id = params.get("conversation_id", params.get("id"))
        if not isinstance(convo_id, int) or isinstance(convo_id, bool):
            raise _invalid("conversation id required")
        conversation = storage.get_conversation(convo_id)
        if conversation is None:
            raise _invalid("对话不存在或已被删除。")
        return conversation

    @staticmethod
    def _current_model() -> str:
        return storage.get_setting("last_model") or config.OLLAMA_MODEL

    @staticmethod
    def _auto_title(convo_id: int, text: str) -> None:
        title = " ".join(text.split())[:_TITLE_CHARS]
        if title:
            storage.update_conversation_title(convo_id, title)
