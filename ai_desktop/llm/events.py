"""Immutable request snapshots and explicit streaming outcomes."""
from dataclasses import dataclass
from enum import Enum

from ai_desktop.llm.ollama_protocol import ModelTurn
from ai_desktop.llm.thinking import ThinkSetting


class EventKind(str, Enum):
    THINKING = "thinking"
    CONTENT = "content"
    COMPLETE = "complete"
    LIMITED = "limited"
    ERROR = "error"
    CANCELLED = "cancelled"


class ErrorCode(str, Enum):
    CONNECTION = "connection"
    HTTP = "http"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    IMAGE = "image"
    SERVER = "server"
    INTERNAL = "internal"


class ResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMITED = "limited"


@dataclass(frozen=True)
class RequestMessage:
    role: str
    content: str
    id: int = 0
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    conversation_id: int
    agent_id: str
    system_prompt: str
    messages: tuple[RequestMessage, ...]
    base_url: str
    model: str
    timeout: int
    think: bool | str | None
    keep_alive: str
    options: tuple[tuple[str, int | float], ...]
    created_at: float
    connect_timeout: float = 10.0
    run_id: str = ""
    step_id: str = ""
    origin: str = "chat"
    action_id: str | None = None
    think_setting: ThinkSetting = ThinkSetting()
    think_source: str = "模型默认"

    def matches(self, event) -> bool:
        return (self.run_id, self.step_id, self.request_id, self.conversation_id) == (
            event.run_id, event.step_id, event.request_id, event.conversation_id,
        )


@dataclass(frozen=True)
class StreamEvent:
    request_id: str
    kind: EventKind
    text: str = ""
    error_code: ErrorCode | None = None
    turn: ModelTurn | None = None
    run_id: str = ""
    step_id: str = ""
    conversation_id: int = 0
    seq: int = 0


@dataclass(frozen=True)
class ChatResult:
    request_id: str
    status: ResultStatus
    text: str = ""
    error: str = ""
    error_code: ErrorCode | None = None
    turn: ModelTurn | None = None
    run_id: str = ""
    step_id: str = ""
    conversation_id: int = 0

    @property
    def ok(self) -> bool:
        return self.status == ResultStatus.SUCCEEDED
