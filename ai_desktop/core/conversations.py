"""Conversation methods for the Workspace: list / open / create / delete / rename / send.

Reads and writes the same SQLite rows as the Qt app, including answer generations, so history
stays interchangeable between the two shells during the migration. Sends are tool-free in P0c.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from ai_desktop import config
from ai_desktop.core import protocol
from ai_desktop.core.admission import check_admission
from ai_desktop.core.capabilities import vision_capable
from ai_desktop.core.runs import RESULT_BYTES, RESULT_TOOLS, RunService, _Message
from ai_desktop.core.search import SearchService, conversation_flag
from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.llm.events import ChatResult, ResultStatus
from ai_desktop.llm.run_types import RunEvent, RunEventKind
from ai_desktop.llm.tool_run import assemble_tool_run
from ai_desktop.services.http_search import HttpSearchExecutor
from ai_desktop.services.session_objects import SessionObjects
from ai_desktop.services.task_admission import TaskAuthorization, TaskModelSettings
from ai_desktop.utils import images as image_utils
from ai_desktop.utils import storage

_TITLE_CHARS = 30


def _invalid(message: str) -> protocol.ProtocolError:
    return protocol.ProtocolError(protocol.ERR_INVALID, message)


def _existing_images(message: storage.Message) -> list[str]:
    """Absolute paths of the message's managed images that are still on disk."""
    found: list[str] = []
    for path in message.images or []:
        relative = image_utils.managed_relative_path(path)
        resolved = image_utils.resolve_managed_path(relative) if relative else None
        if resolved is not None and resolved.is_file():
            found.append(str(resolved))
    return found


def collapse_versions(messages: list[storage.Message]) -> list[dict[str, Any]]:
    """One answer per user turn: the active generation if any, else the latest assistant message."""
    turns: list[dict[str, Any]] = []
    pending: list[storage.Message] = []
    last_user: storage.Message | None = None

    def flush() -> None:
        if last_user is None:
            return
        if not pending:
            # No answer text (the model failed, said nothing, or the run was stopped) but lookups did return:
            # keep what they found so a reopened conversation still shows it.
            saved = [g for g in storage.list_generations(last_user.id) if (g.config_snapshot or {}).get("tool_results")]
            if saved:
                turns.append({"id": -saved[-1].id, "role": "assistant", "content": "",
                              "tool_results": saved[-1].config_snapshot["tool_results"]})
            return
        active = storage.get_active_generation(last_user.id)
        chosen = next((m for m in pending if active and m.id == active.assistant_message_id), pending[-1])
        turn = {"id": chosen.id, "role": "assistant", "content": chosen.content}
        shown = None
        if active and active.assistant_message_id == chosen.id:
            shown = (active.config_snapshot or {}).get("tool_results")
        if shown:
            turn["tool_results"] = shown
        turns.append(turn)

    for message in messages:
        if message.role == "user":
            flush()
            pending = []
            last_user = message
            turns.append({"id": message.id, "role": "user", "content": message.content,
                          "images": _existing_images(message)})
        elif message.role == "assistant" and last_user is not None:
            pending.append(message)
    flush()
    return turns


class ConversationService:
    def __init__(self, runs: RunService, search: SearchService | None = None) -> None:
        self._runs = runs
        self._search = search or SearchService()
        self._busy: set[int] = set()
        self._lock = threading.Lock()
        self._session_objects: dict[int, SessionObjects] = {}

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
                "messages": collapse_versions(conversation.messages), "search": conversation_flag(conversation.id)}

    def agents(self, _params: dict[str, Any]) -> dict[str, Any]:
        builtin = {a.id for a in self._runs.agent_manager.builtin_agents}
        return {"agents": [{"id": a.id, "name": a.name, "icon": a.icon, "system_prompt": a.system_prompt,
                            "builtin": a.id in builtin} for a in self._runs.agents]}

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

    def set_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation = self._require(params)
        agent_id = params.get("agent_id")
        if not any(a.id == agent_id for a in self._runs.agents):
            raise _invalid("助手不存在。")
        with self._lock:
            if conversation.id in self._busy:
                raise _invalid("这个对话正在生成回答。")
        storage.update_conversation_agent(conversation.id, agent_id)
        return {}

    def send(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "send")
        if mode not in ("send", "regenerate", "edit"):
            raise _invalid("mode must be send, regenerate or edit")
        text = params.get("text")
        if mode != "regenerate" and (not isinstance(text, str) or not text.strip()):
            raise _invalid("text required")
        raw_images = params.get("images", [])
        if (not isinstance(raw_images, list) or not all(isinstance(p, str) for p in raw_images)
                or (raw_images and mode != "send")):
            raise _invalid("images must be a list of file paths on a normal send")
        conversation = self._require(params)
        last_user = next((m for m in reversed(conversation.messages) if m.role == "user"), None)
        if mode != "send" and last_user is None:
            raise _invalid("没有可以重新生成的消息。")
        with self._lock:
            if conversation.id in self._busy:
                raise _invalid("这个对话正在生成回答。")
            self._busy.add(conversation.id)
        try:
            if mode == "edit":
                self._drop_last_turn(conversation, last_user)
                conversation = self._require(params)
                last_user = None
            return self._send(conversation, text if mode != "regenerate" else last_user.content,
                              params.get("model"), regenerate_of=last_user if mode == "regenerate" else None,
                              image_sources=raw_images)
        except Exception:
            with self._lock:
                self._busy.discard(conversation.id)
            raise

    @staticmethod
    def _drop_last_turn(conversation: storage.Conversation, last_user: storage.Message) -> None:
        """Editing the last message replaces that turn: its question and every answer version go."""
        after = False
        for message in list(conversation.messages):
            after = after or message.id == last_user.id
            if after:
                storage.delete_message(message.id)

    def _send(self, conversation: storage.Conversation, text: str, model_override: Any,
              regenerate_of: storage.Message | None = None, image_sources: list[str] | None = None
              ) -> dict[str, Any]:
        agent = next((a for a in self._runs.agents if a.id == conversation.agent_id), self._runs.agents[0])
        resolved = self._runs.resolve_model(
            model_override if isinstance(model_override, str) and model_override else self._current_model(),
            agent_profile_id=agent.profile_id,
        )
        images: list[str] = []
        if image_sources:
            if vision_capable(resolved.model) is False:
                raise _invalid(f"当前模型 {resolved.model} 不支持看图，请在顶部切换到支持视觉的模型。")
            try:
                images = [image_utils.store_prepared_png(path) for path in image_sources]
            except (image_utils.AttachmentError, OSError) as exc:
                raise _invalid(str(exc)) from exc
        if regenerate_of is not None:
            earlier = [m for m in conversation.messages if m.id < regenerate_of.id]
            history = collapse_versions(earlier)
            user_message = regenerate_of
        else:
            history = collapse_versions(conversation.messages)
            user_message = storage.save_message(conversation.id, "user", text, images or None)
        if not conversation.messages and not conversation.title:
            self._auto_title(conversation.id, text)
        messages = [_Message(m["role"], m["content"]) for m in history if m["content"]] + [
            _Message("user", text, user_message.id, tuple(images))]
        admission, tools_note = self._admit_tools(agent, resolved)
        if admission is None and conversation_flag(conversation.id):
            tools_note = tools_note or "联网搜索未启用：当前模型不满足工具要求。"
        if admission is None:
            request = ChatClient(model=resolved.model).create_request(
                messages, agent.system_prompt, conversation_id=conversation.id, agent_id=agent.id,
                think=resolved.think, think_setting=resolved.think_setting, think_source=resolved.think_source,
                options=resolved.options,
            )
            context, broker, tool_names = None, None, []
        else:
            granted = admission[1]
            request = ChatClient(model=granted["model"]).create_request(
                messages, agent.system_prompt, conversation_id=conversation.id, agent_id=agent.id,
                think=granted["think"], think_setting=granted["think_setting"],
                think_source=granted["think_source"], options=granted["options"],
            )
            request = replace(request, options=tuple(granted["options"].items()))
            session = self._session_objects.setdefault(conversation.id, SessionObjects())
            search_settings, search_note = None, ""
            if conversation_flag(conversation.id):
                if self._search.has_key():
                    search_settings = self._search.settings()
                else:
                    search_note = "联网搜索未启用：没有可用的搜索密钥。"
            assembly = assemble_tool_run(
                request, agent.system_prompt, tools_admitted=True, native_tools=True,
                make_broker=self._runs.new_broker, session_context=session.render(),
                desktop_runner=self._runs.desktop_runner, search_settings=search_settings,
                search_executor_factory=lambda cfg: HttpSearchExecutor(cfg, credentials=self._search.credentials))
            tools_note = tools_note or search_note
            request, context, broker = assembly.request, assembly.context, assembly.confirmations
            tool_names = [tool.name for tool in context.tools]
            if not tool_names:
                context, broker = None, None

        shown_results: list[dict[str, str]] = []
        observe = self._observer(conversation.id)

        def on_tool(event: RunEvent) -> None:
            observe(event)
            # What the lookup tools really returned, kept with the answer so a reopened conversation shows it again.
            if (event.kind == RunEventKind.TOOL_FINISHED and event.tool_name in RESULT_TOOLS
                    and event.output is not None and not event.output.error
                    and len(event.output.text) <= RESULT_BYTES):
                shown_results.append({"tool": event.tool_name, "output": event.output.text})

        def persist(result: ChatResult) -> None:
            snapshot = {"model": request.model, "run_id": request.run_id, "allowed_tools": tool_names,
                        "tool_results": shown_results, "admission": admission[0].record() if admission else None}
            try:
                if result.ok and result.text:
                    assistant = storage.save_message(conversation.id, "assistant", result.text)
                    storage.save_generation(
                        user_message.id, request.request_id, status="succeeded", answer=result.text,
                        assistant_message_id=assistant.id, active=True,
                        config_snapshot=snapshot)
                elif shown_results:
                    storage.save_generation(
                        user_message.id, request.request_id,
                        status="cancelled" if result.status == ResultStatus.CANCELLED else "failed",
                        config_snapshot=snapshot)
            finally:
                with self._lock:
                    self._busy.discard(conversation.id)

        self._runs.launch(request, on_finished=persist, context=context, broker=broker,
                          on_tool=on_tool)
        return {"run_id": request.run_id, "conversation_id": conversation.id, "user_message_id": user_message.id,
                "model": request.model, "tools": tool_names, "tools_note": tools_note}

    def _admit_tools(self, agent, resolved):
        """(admission, worker kwargs) when this message may use the native desktop tools, else (None, why).

        Without tools the message still runs; the reason is returned so the shell can say so.
        """
        if not (config.DESKTOP_TOOLS_ENABLED and config.CHAT_TOOLS_ENABLED):
            return None, ""
        try:
            settings = TaskModelSettings.from_config(resolved)
            admission = check_admission(config.OLLAMA_BASE_URL, resolved.model, settings)
            kwargs = TaskAuthorization(agent_id=agent.id, native=True).worker_kwargs(
                admission, config.OLLAMA_BASE_URL, agent_id=agent.id, origin="chat", model=resolved.model,
                settings=settings)
        except ValueError as exc:
            return None, f"本次未启用工具：{exc}"
        return (admission, kwargs), ""

    def _observer(self, conversation_id: int):
        session = self._session_objects.setdefault(conversation_id, SessionObjects())

        def observe(event: RunEvent) -> None:
            if event.kind == RunEventKind.TOOL_FINISHED and event.output is not None:
                session.observe(event.tool_name, event.arguments_json, event.output.text, event.output.error)
        return observe

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
