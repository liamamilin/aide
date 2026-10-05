"""Shared Ollama message assembly for Qt and synchronous transports.

The admitted provider emits complete tool-call objects in stream frames. String
argument deltas are deliberately rejected rather than guessed or concatenated.
Calls retain wire order and optional provider IDs; equal arguments are not IDs.
"""
import json
import uuid
from dataclasses import dataclass


class StreamProtocolError(Exception):
    """A malformed response or incomplete model turn."""


@dataclass(frozen=True)
class ToolCall:
    local_call_id: str
    name: str
    arguments_json: str
    provider_call_id: str | None = None
    provider_index: int | None = None
    call_type: str | None = None

    def wire(self):
        value = {"function": {"name": self.name, "arguments": json.loads(self.arguments_json)}}
        if self.provider_call_id is not None:
            value["id"] = self.provider_call_id
        if self.provider_index is not None:
            value["function"]["index"] = self.provider_index
        if self.call_type is not None:
            value["type"] = self.call_type
        return value


@dataclass(frozen=True)
class ModelTurn:
    content: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    done_reason: str | None
    metrics: tuple[tuple[str, int], ...] = ()

    def wire(self):
        message = {"role": "assistant", "content": self.content}
        if self.thinking:
            message["thinking"] = self.thinking
        if self.tool_calls:
            message["tool_calls"] = [call.wire() for call in self.tool_calls]
        return message


def response_message(data: object) -> tuple[dict, str]:
    if not isinstance(data, dict):
        raise StreamProtocolError("Response must be an object")
    if "error" in data:
        error = data["error"]
        if not isinstance(error, str) or not error:
            raise StreamProtocolError("Invalid server error")
        return {}, error
    message = data.get("message", {})
    if not isinstance(message, dict):
        raise StreamProtocolError("Invalid message")
    if message.get("role", "assistant") != "assistant":
        raise StreamProtocolError("Invalid response role")
    for key in ("content", "thinking"):
        if not isinstance(message.get(key, ""), str):
            raise StreamProtocolError(f"Invalid {key}")
    if not isinstance(data.get("done", False), bool):
        raise StreamProtocolError("Invalid completion flag")
    if "message" not in data and data.get("done") is not True:
        raise StreamProtocolError("Missing message")
    return message, ""


class TurnAssembler:
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self):
        self._content = []
        self._thinking = []
        self._calls = []
        self._provider_ids = set()
        self._provider_indices = set()
        self._bytes = 0
        self.turn = None

    def accept(self, data) -> tuple[dict, str]:
        if self.turn is not None:
            raise StreamProtocolError("Frame after completion")
        message, error = response_message(data)
        if error:
            return message, error
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            raise StreamProtocolError("Invalid tool call list")
        additions = []
        for value in calls:
            if not isinstance(value, dict) or not isinstance(value.get("function"), dict):
                raise StreamProtocolError("Invalid tool call")
            function = value["function"]
            name = function.get("name")
            args = function.get("arguments")
            provider_id = value.get("id")
            provider_index = function.get("index")
            call_type = value.get("type")
            if not isinstance(name, str) or not name or not isinstance(args, dict):
                raise StreamProtocolError("Expected complete tool call")
            if "id" in value and (not isinstance(provider_id, str) or not provider_id):
                raise StreamProtocolError("Invalid provider call ID")
            if provider_id is not None and provider_id in self._provider_ids:
                raise StreamProtocolError("Repeated provider call ID")
            if provider_id is not None:
                self._provider_ids.add(provider_id)
            if "index" in function:
                if type(provider_index) is not int or provider_index < 0 or provider_index in self._provider_indices:
                    raise StreamProtocolError("Invalid or repeated provider call index")
                self._provider_indices.add(provider_index)
            if "type" in value and call_type != "function":
                raise StreamProtocolError("Invalid tool call type")
            try:
                arguments = json.dumps(args, ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError):
                raise StreamProtocolError("Invalid tool arguments") from None
            additions.append(ToolCall(uuid.uuid4().hex, name, arguments, provider_id, provider_index, call_type))
        if len(self._calls) + len(additions) > 16:
            raise StreamProtocolError("Too many tool calls")
        self._bytes += len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        if self._bytes > self.MAX_BYTES:
            raise StreamProtocolError("Model response too large")
        self._content.append(message.get("content", ""))
        self._thinking.append(message.get("thinking", ""))
        self._calls.extend(additions)
        if data.get("done"):
            reason = data.get("done_reason")
            if reason not in (None, "stop", "length"):
                raise StreamProtocolError("Unknown completion reason")
            metrics = []
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                        "prompt_eval_duration", "eval_duration"):
                if key in data:
                    if type(data[key]) is not int or data[key] < 0:
                        raise StreamProtocolError("Invalid completion metrics")
                    metrics.append((key, data[key]))
            self.turn = ModelTurn("".join(self._content), "".join(self._thinking), tuple(self._calls),
                                  reason, tuple(metrics))
        return message, ""
