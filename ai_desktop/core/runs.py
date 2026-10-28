"""Quick-action runs: one streamed model turn through the shared RunLoop, no Qt.

P0b is tool-free and ephemeral (nothing is written to conversation history); the
shell shows the streamed text and offers copy / continue. Cancel and stale-event
filtering use the same request/run/step IDs as the Qt app.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai_desktop import config
from ai_desktop.agent_manager import AgentManager
from ai_desktop.core import protocol
from ai_desktop.core.capabilities import thinking_capability
from ai_desktop.desktop.runner import run_command
from ai_desktop.llm.chat_client import ChatClient, _payload
from ai_desktop.llm.events import ChatResult, EventKind, ResultStatus
from ai_desktop.llm.run_loop import RunLoop
from ai_desktop.llm.run_types import RunContext, RunEvent, RunEventKind
from ai_desktop.llm.sync_stream import SyncChatTransport
from ai_desktop.services.action_service import ActionService
from ai_desktop.services.command_confirmation import CommandConfirmation, ConfirmationBroker
from ai_desktop.services.model_profiles import ModelProfileManager
from ai_desktop.utils import storage

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class _Message:
    role: str
    content: str
    id: int = 0
    images: tuple[str, ...] = ()


class _ActiveRun:
    def __init__(self, broker: ConfirmationBroker | None = None) -> None:
        self.cancelled = threading.Event()
        self.transport: SyncChatTransport | None = None
        self.broker = broker

    def cancel(self) -> None:
        self.cancelled.set()
        transport = self.transport
        if transport is not None:
            transport.cancel()


class RunService:
    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self._actions = ActionService()
        self._agents = AgentManager()
        self._profiles = ModelProfileManager()
        # Injected in tests so no real mdfind / osascript ever runs.
        self.desktop_runner = run_command
        self._runs: dict[str, _ActiveRun] = {}
        self._lock = threading.Lock()

    def list_actions(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"actions": [{"id": a.id, "name": a.name, "agent_id": a.agent_id}
                            for a in self._actions.visible_actions]}

    @property
    def action_service(self):
        return self._actions

    def start(self, params: dict[str, Any]) -> dict[str, Any]:
        action_id, material = params.get("action_id"), params.get("material")
        if not isinstance(action_id, str) or not isinstance(material, str):
            raise protocol.ProtocolError(protocol.ERR_INVALID, "run.start needs action_id and material")
        try:
            plan = self._actions.build_request_plan(action_id, material, self._agents.all_agents)
        except (LookupError, ValueError) as exc:
            raise protocol.ProtocolError(protocol.ERR_INVALID, str(exc)) from exc
        resolved = self.resolve_model(
            storage.get_setting("last_model") or config.OLLAMA_MODEL,
            agent_profile_id=plan.agent.profile_id, action_profile_id=plan.action_profile_id,
        )
        request = ChatClient(model=resolved.model).create_request(
            [_Message("user", plan.material)], plan.system_prompt, agent_id=plan.agent.id,
            think=resolved.think, think_setting=resolved.think_setting, think_source=resolved.think_source,
            origin="action", action_id=plan.action.id, options=resolved.options,
        )
        self._actions.record_use(plan.action.id)
        self.launch(request)
        return {"run_id": request.run_id, "model": resolved.model, "agent": plan.agent.name,
                "warnings": list(plan.warnings) + list(resolved.warnings)}

    def launch(self, request, on_finished: Callable[[ChatResult], None] | None = None, *,
               context: RunContext | None = None, broker: ConfirmationBroker | None = None,
               on_tool: Callable[[RunEvent], None] | None = None) -> None:
        """Run one request on its own thread; tool-free unless an admitted ``context`` is passed.

        ``on_finished`` runs before ``run.finished``; ``broker`` is the confirmation broker the context's
        tools ask through, so ``run.confirm`` can answer it.
        """
        run = _ActiveRun(broker)
        with self._lock:
            self._runs[request.run_id] = run
        threading.Thread(target=self._execute, args=(context or RunContext.create(request), run, on_finished, on_tool),
                         name=f"aide-run-{request.run_id[:6]}", daemon=True).start()

    def new_broker(self) -> ConfirmationBroker:
        return ConfirmationBroker(self._emit_confirmation)

    def _emit_confirmation(self, request: CommandConfirmation) -> None:
        # The preview is the exact action text built by trusted code, never model-written prose.
        self._emit("run.confirm_request", {
            "run_id": request.run_id, "confirmation_id": request.id, "call_id": request.tool_call_id,
            "preview": request.command, "reason": request.reason, "expires_in": 600,
        })

    def confirm(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id, confirmation_id, approved = params.get("run_id"), params.get("confirmation_id"), params.get("approved")
        if not isinstance(run_id, str) or not isinstance(confirmation_id, str) or type(approved) is not bool:
            raise protocol.ProtocolError(protocol.ERR_INVALID, "run_id, confirmation_id and approved are required")
        with self._lock:
            run = self._runs.get(run_id)
        broker = run.broker if run is not None else None
        pending = next((item for item in broker.pending if item.id == confirmation_id), None) if broker else None
        if pending is None or not broker.respond(pending, approved):
            raise protocol.ProtocolError(protocol.ERR_INVALID, "这个确认已过期或已处理。")
        return {}

    def resolve_model(self, global_model: str, *, agent_profile_id=None, action_profile_id=None):
        """Profile + think resolution with the model's real thinking capability (cached /api/show)."""
        return self._profiles.resolve(
            global_model=global_model, agent_profile_id=agent_profile_id, action_profile_id=action_profile_id,
            thinking_capability=thinking_capability,
        )

    @property
    def agents(self):
        return self._agents.all_agents

    @property
    def agent_manager(self) -> AgentManager:
        return self._agents

    @property
    def profiles(self) -> ModelProfileManager:
        return self._profiles

    def cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(params.get("run_id", ""))
        if run is not None:
            run.cancel()
        return {"cancelled": run is not None}

    def _execute(self, context: RunContext, active: _ActiveRun,
                 on_finished: Callable[[ChatResult], None] | None = None,
                 on_tool: Callable[[RunEvent], None] | None = None) -> None:
        run_id = context.request.run_id
        started = time.monotonic()

        def deliver(event) -> None:
            kind = "thinking" if event.kind == EventKind.THINKING else "content"
            self._emit("run.delta", {"run_id": run_id, "kind": kind, "text": event.text})

        def model_step(request, payload, deadline):
            transport = SyncChatTransport(request, on_stream=lambda ev: loop.forward_stream(ev, deliver),
                                          allow_tools=bool(context.tools))
            active.transport = transport
            timer = None
            if deadline is not None:
                timer = threading.Timer(max(0.001, deadline - time.monotonic()), transport.limit)
                timer.daemon = True
                timer.start()
            try:
                if active.cancelled.is_set():
                    transport.cancel()
                    return transport.result
                return transport.run(payload)
            finally:
                if timer is not None:
                    timer.cancel()

        def on_event(event: RunEvent) -> None:
            if event.kind not in {RunEventKind.TOOL_STARTED, RunEventKind.TOOL_UPDATED, RunEventKind.TOOL_FINISHED}:
                return
            self._emit("run.tool", {"run_id": run_id, "call_id": event.local_call_id, "tool": event.tool_name,
                                    "state": event.kind.value, "status": event.status})
            if on_tool is not None:
                try:
                    on_tool(event)
                except Exception:
                    logger.exception("tool observer failed")

        loop = RunLoop(context, active.cancelled, model_step, on_event=on_event)
        try:
            result = loop.run(lambda: _payload(context.request, stream=True))
        except Exception as exc:
            logger.exception("run %s crashed", run_id)
            result = ChatResult(context.request.request_id, ResultStatus.FAILED, error=str(exc))
        finally:
            with self._lock:
                self._runs.pop(run_id, None)
        if on_finished is not None:
            try:
                on_finished(result)
            except Exception:
                logger.exception("persisting run %s failed", run_id)
        self._emit("run.finished", {
            "run_id": run_id, "conversation_id": context.request.conversation_id,
            "status": result.status.value, "text": result.text, "error": result.error,
            "error_code": result.error_code.value if result.error_code else "",
            "seconds": round(time.monotonic() - started, 2),
        })
