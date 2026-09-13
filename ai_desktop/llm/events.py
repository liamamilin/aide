"""Immutable request snapshots and explicit streaming outcomes."""
from dataclasses import dataclass
from enum import Enum


class EventKind(str, Enum):
    THINKING = "thinking"
    CONTENT = "content"
    COMPLETE = "complete"
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
    think: bool
    keep_alive: str
    options: tuple[tuple[str, int | float], ...]
    created_at: float
    connect_timeout: float = 10.0


@dataclass(frozen=True)
class StreamEvent:
    request_id: str
    kind: EventKind
    text: str = ""
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class ChatResult:
    request_id: str
    status: ResultStatus
    text: str = ""
    error: str = ""
    error_code: ErrorCode | None = None

    @property
    def ok(self) -> bool:
        return self.status == ResultStatus.SUCCEEDED
