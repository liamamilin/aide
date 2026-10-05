"""Single-use command confirmations, bound to exact run/call/config identity."""
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ConfirmationOutcome(str, Enum):
    APPROVED = 'approved'
    DENIED = 'denied'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True)
class CommandConfirmation:
    id: str
    run_id: str
    step_id: str
    request_id: str
    conversation_id: int
    tool_call_id: str
    command: str = field(repr=False)
    workspace: str
    policy: str
    timeout: int
    reason: str
    fingerprint: str
    expires_at: float

    @classmethod
    def create(cls, context, command, timeout, reason, *, clock=time.monotonic):
        snapshot = context.execution
        request = context.request
        identity = [request.run_id, request.step_id, request.request_id, request.conversation_id,
                    context.call.local_call_id, command, snapshot.record(), timeout]
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return cls(uuid.uuid4().hex, request.run_id, request.step_id, request.request_id,
                   request.conversation_id, context.call.local_call_id, command, snapshot.workspace,
                   snapshot.policy.value, timeout, reason, fingerprint, clock() + 600)


class ConfirmationBroker:
    def __init__(self, on_request=None, *, clock=time.monotonic):
        self.on_request = on_request
        self.clock = clock
        self._condition = threading.Condition()
        self._pending = {}
        self._used = set()

    @property
    def pending(self):
        with self._condition:
            return tuple(item[0] for item in self._pending.values())

    def respond(self, request, approved):
        if type(approved) is not bool or not isinstance(request, CommandConfirmation):
            return False
        with self._condition:
            pending = self._pending.get(request.id)
            if (pending is None or pending[0] != request or pending[1] is not None
                    or self.clock() >= request.expires_at):
                return False
            pending[1] = ConfirmationOutcome.APPROVED if approved else ConfirmationOutcome.DENIED
            self._condition.notify_all()
            return True

    def wait(self, request, cancelled):
        if cancelled.is_set():
            return ConfirmationOutcome.CANCELLED
        if self.on_request is None:
            return ConfirmationOutcome.UNAVAILABLE
        with self._condition:
            if request.id in self._used:
                raise ValueError('Confirmation is already pending')
            self._used.add(request.id)
            self._pending[request.id] = [request, None]
        try:
            # Do not hold the lock while delivering to the UI/caller.
            self.on_request(request)
            with self._condition:
                while True:
                    if cancelled.is_set():
                        return ConfirmationOutcome.CANCELLED
                    if self.clock() >= request.expires_at:
                        return ConfirmationOutcome.EXPIRED
                    answer = self._pending[request.id][1]
                    if answer is not None:
                        return answer
                    self._condition.wait(min(0.05, request.expires_at - self.clock()))
        finally:
            with self._condition:
                self._pending.pop(request.id, None)
