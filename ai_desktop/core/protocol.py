"""NDJSON wire protocol between the native shell and the Python core.

One JSON object per line, UTF-8. Three envelope shapes:

- request:  ``{"id": "<str>", "method": "<name>", "params": {...}}``
- response: ``{"id": "<str>", "result": {...}}`` or ``{"id": "<str>", "error": {"code", "message"}}``
- event:    ``{"event": "<name>", "data": {...}}`` (no id; core -> shell notifications)

Either side may send requests (the core asks the shell to confirm a command, for
example). Core-originated request ids start with ``c`` so they never collide with
the shell's ids. stdout carries protocol only; logs go to stderr.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1

ERR_PARSE = "parse_error"
ERR_INVALID = "invalid_request"
ERR_UNKNOWN_METHOD = "unknown_method"
ERR_INTERNAL = "internal_error"


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Request:
    id: str
    method: str
    params: dict[str, Any]


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_request(line: str) -> Request:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(ERR_PARSE, f"invalid JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(ERR_INVALID, "message must be a JSON object")
    req_id, method, params = obj.get("id"), obj.get("method"), obj.get("params", {})
    if not isinstance(req_id, str) or not req_id:
        raise ProtocolError(ERR_INVALID, "request needs a non-empty string id")
    if not isinstance(method, str) or not method:
        raise ProtocolError(ERR_INVALID, "request needs a method")
    if not isinstance(params, dict):
        raise ProtocolError(ERR_INVALID, "params must be an object")
    return Request(req_id, method, params)


def result(req_id: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"id": req_id, "result": value}


def error(req_id: str | None, code: str, message: str) -> dict[str, Any]:
    return {"id": req_id, "error": {"code": code, "message": message}}


def event(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"event": name, "data": data or {}}
