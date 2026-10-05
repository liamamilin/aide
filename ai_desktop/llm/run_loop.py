"""Single sequential run engine, independent of the network/Qt implementation.

Executors are injected trusted code. The UI currently constructs empty-tool
runs; tool tests use synthetic executors, never an actual shell or paid API.
"""
import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from ai_desktop.llm.active_budget import ActiveBudget
from ai_desktop.llm.chat_client import _exception_error
from ai_desktop.llm.events import ChatResult, ErrorCode, EventKind, RequestContext, ResultStatus
from ai_desktop.llm.ollama_protocol import ModelTurn
from ai_desktop.llm.run_types import (
    RunContext,
    RunEvent,
    RunEventKind,
    ToolExecutionContext,
    ToolOutput,
    validate_tool_arguments,
)

# Only explicit, numeric server rejections permit one recovery. A length
# completion, timeout, generic HTTP error or the word "context" never does.
_CONTEXT_REJECTION = re.compile(
    r"^input length \d+ exceeds (?:the )?(?:maximum )?context (?:length|window)(?: of)? \d+[.!]?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProtocolBlock:
    messages: tuple[str, ...]
    required: bool = False

    @classmethod
    def create(cls, messages: list[dict], *, required=False):
        return cls(tuple(json.dumps(message, ensure_ascii=False, allow_nan=False) for message in messages), required)

    def wire(self):
        return [json.loads(message) for message in self.messages]


def initial_blocks(messages: list[dict]) -> list[ProtocolBlock]:
    """Group each existing user turn with its answer; pin system and last user."""
    blocks = []
    current = []
    for message in messages:
        if message["role"] in {"system", "user"} and current:
            blocks.append(ProtocolBlock.create(current, required=current[0]["role"] == "system"))
            current = []
        current.append(message)
    if current:
        blocks.append(ProtocolBlock.create(current, required=current[0]["role"] == "system"))
    last_user = next((i for i in range(len(blocks) - 1, -1, -1)
                      if any(m["role"] == "user" for m in blocks[i].wire())), None)
    if last_user is not None:
        blocks[last_user] = replace(blocks[last_user], required=True)
    return blocks


def bounded_tool_output(output: ToolOutput, limit: int) -> ToolOutput:
    encoded = output.text.encode("utf-8")
    if len(encoded) <= limit:
        return output
    marker = "\n[工具结果已按字节预算截断]"
    if len(marker.encode("utf-8")) > limit:
        return ToolOutput(encoded[:limit].decode("utf-8", errors="ignore"), output.error)
    text = encoded[:max(0, limit - len(marker.encode("utf-8")))].decode("utf-8", errors="ignore")
    return ToolOutput(text + marker, output.error)


class RunLoop:
    def __init__(self, context: RunContext, cancelled: threading.Event,
                 model_step: Callable[[RequestContext, dict, float | None], ChatResult], *,
                 on_event: Callable[[RunEvent], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.context = context
        self.cancelled = cancelled
        self.model_step = model_step
        self.on_event = on_event or (lambda event: None)
        self.clock = clock
        self.current_request = context.request
        self.result = None
        self.model_rounds = 0
        self.model_requests = 0
        self.tool_calls = 0
        self.search_calls = 0
        self._seq = 0
        self._budget = None
        self._recovered = False
        self._stream_sequences = {}

    @property
    def _deadline(self):
        return self._budget.deadline if self._budget is not None else None

    def forward_stream(self, event, on_stream):
        """Merge stream chunks and lifecycle events into one run-wide sequence."""
        if self.result is not None or not self.current_request.matches(event):
            return
        last = self._stream_sequences.get(event.request_id, 0)
        if event.seq <= last:
            return
        self._stream_sequences[event.request_id] = event.seq
        if event.kind not in {EventKind.THINKING, EventKind.CONTENT}:
            return
        self._emit(RunEventKind.THINKING if event.kind == EventKind.THINKING else RunEventKind.CONTENT)
        on_stream(replace(event, seq=self._seq))

    def _emit(self, kind, *, call=None, status="", output=None, payload=None):
        self._seq += 1
        request = self.current_request
        self.on_event(RunEvent(request.run_id, request.conversation_id, request.step_id, request.request_id,
                               self._seq, kind, call.local_call_id if call else None,
                               call.provider_call_id if call else None, status,
                               call.name if call else "", call.arguments_json if call else "", output,
                               json.dumps(payload, ensure_ascii=False) if payload is not None else ''))

    def _finish(self, status, *, text="", error="", code=None, turn=None):
        if self.result is None:
            request = self.current_request
            self.result = ChatResult(request.request_id, status, text, error, code, turn,
                                     request.run_id, request.step_id, request.conversation_id)
            self._emit(RunEventKind.FINISHED, status=status.value,
                       payload={'error_code': code.value if code else ''})
        return self.result

    def _interrupted(self, text="", turn=None):
        if self.cancelled.is_set():
            return self._finish(ResultStatus.CANCELLED, text=text, turn=turn)
        if self._deadline is not None and self.clock() >= self._deadline:
            return self._finish(ResultStatus.LIMITED, text=text, turn=turn, error="任务达到活动时间上限。")
        return None

    @staticmethod
    def _flatten(blocks):
        return [message for block in blocks for message in block.wire()]

    def _input_estimate(self, blocks, schemas):
        # Conservative UTF-8 byte estimate plus per-message overhead, not a
        # provider tokenizer or a promise about undocumented server shifting.
        messages = self._flatten(blocks)
        size = len(json.dumps({"messages": messages, "tools": schemas}, ensure_ascii=False).encode())
        return size + 256 * len(messages)

    def _trim(self, blocks, schemas, *, force=False):
        options = dict(self.context.request.options)
        ceiling = int(options.get("num_ctx", 8192)) - int(options.get("num_predict", 1024))
        ceiling -= self.context.limits.context_reserve
        changed = False
        while force or self._input_estimate(blocks, schemas) > ceiling:
            index = next((i for i, block in enumerate(blocks) if not block.required), None)
            if index is None:
                if not schemas or not self._shrink_results(blocks):
                    return False
            else:
                del blocks[index]
            changed, force = True, False
            if not schemas:
                # Ordinary chat retains its historic output configuration.
                # An explicit input rejection gets one whole-block reduction,
                # independent of the potentially larger output upper bound.
                break
        if changed:
            self._emit(RunEventKind.CONTEXT_REDUCED)
        return True

    @staticmethod
    def _shrink_results(blocks):
        """Reduce only excerpts in the pinned complete tool block, never pairs."""
        for index in range(len(blocks) - 1, -1, -1):
            block = blocks[index]
            messages = block.wire()
            if not block.required or not any(message.get("tool_calls") for message in messages):
                continue
            outputs = [message for message in messages if message["role"] == "tool"]
            largest = max((len(message["content"].encode()) for message in outputs), default=0)
            if largest <= 256:
                return False
            budget = max(256, largest // 2)
            for message in outputs:
                message["content"] = bounded_tool_output(ToolOutput(message["content"]), budget).text
            blocks[index] = ProtocolBlock.create(messages, required=True)
            return True
        return False

    def run(self, payload: dict | Callable[[], dict]) -> ChatResult:
        if self.result is not None:
            return self.result
        self._emit(RunEventKind.STARTED)
        self._budget = ActiveBudget(self.context.limits.active_seconds, clock=self.clock)
        registry = {tool.name: tool for tool in self.context.tools}
        schemas = [tool.wire() for tool in self.context.tools]
        try:
            interrupted = self._interrupted()
            if interrupted is not None:
                return interrupted
            payload = payload() if callable(payload) else payload
            blocks = initial_blocks(payload["messages"])
            while True:
                interrupted = self._interrupted()
                if interrupted is not None:
                    return interrupted
                if self.model_rounds >= self.context.limits.max_model_rounds:
                    return self._finish(ResultStatus.LIMITED, error="达到模型轮次上限。")
                # Preserve the existing single-turn chat payload exactly.
                if registry and not self._trim(blocks, schemas):
                    return self._finish(ResultStatus.LIMITED, error="必要上下文超过任务预算。")
                self.model_rounds += 1
                while True:
                    request = self.current_request
                    wire = {**payload, "messages": self._flatten(blocks), "model": request.model,
                            "think": request.think, "keep_alive": request.keep_alive, "options": dict(request.options)}
                    if schemas:
                        wire["tools"] = [tool.wire() for tool in self.context.tools]
                    else:
                        wire.pop("tools", None)
                    self.model_requests += 1
                    self._emit(RunEventKind.MODEL_STARTED, payload={'input': [
                        {**{key: message[key] for key in ('role', 'content', 'tool_calls', 'tool_call_id')
                            if key in message}, 'image_count': len(message.get('images', []))}
                        for message in wire['messages']]})
                    result = self.model_step(self.current_request, wire, self._deadline)
                    if not self.current_request.matches(result):
                        return self._finish(ResultStatus.FAILED, error="模型步骤身份不匹配。", code=ErrorCode.PROTOCOL)
                    self._emit(RunEventKind.MODEL_FINISHED, status=result.status.value, payload={'output': {
                        'content': result.text, 'thinking': result.turn.thinking if result.turn else '',
                        'done_reason': result.turn.done_reason if result.turn else None,
                        'error_code': result.error_code.value if result.error_code else '',
                        'tool_calls': [{'local_call_id': call.local_call_id, 'name': call.name,
                                        'arguments_json': call.arguments_json,
                                        'provider_call_id': call.provider_call_id}
                                       for call in result.turn.tool_calls] if result.turn else []}})
                    interrupted = self._interrupted(result.text, result.turn)
                    if interrupted is not None:
                        return interrupted
                    explicit_overflow = (result.status == ResultStatus.FAILED and result.error_code == ErrorCode.SERVER
                                         and bool(_CONTEXT_REJECTION.fullmatch(result.error)))
                    if explicit_overflow and not self._recovered:
                        self._recovered = True
                        if not self._trim(blocks, schemas, force=True):
                            return self._finish(ResultStatus.LIMITED, text=result.text,
                                                error="输入过长，必要上下文无法继续缩减。")
                        self.current_request = replace(self.current_request, request_id=uuid.uuid4().hex)
                        continue
                    break
                if not result.ok:
                    return self._finish(result.status, text=result.text, error=result.error,
                                        code=result.error_code, turn=result.turn)
                turn = result.turn
                if turn is None:
                    return self._finish(ResultStatus.FAILED, text=result.text,
                                        error="模型步骤缺少完整终态。", code=ErrorCode.PROTOCOL)
                if turn.done_reason not in (None, "stop", "length"):
                    return self._finish(ResultStatus.FAILED, text=result.text,
                                        error="模型结束原因未知。", code=ErrorCode.PROTOCOL)
                if turn.done_reason == "length":
                    return self._finish(ResultStatus.LIMITED, text=turn.content, turn=turn,
                                        error="达到输出上限，回复尚未完成。")
                if not turn.tool_calls:
                    return self._finish(ResultStatus.SUCCEEDED, text=turn.content, turn=turn)
                if not registry:
                    return self._finish(ResultStatus.FAILED, text=turn.content, turn=turn,
                                        error="本次对话未授权工具调用。", code=ErrorCode.PROTOCOL)
                interrupted = self._execute_turn(turn, blocks, registry)
                if interrupted is not None:
                    return interrupted
                self.current_request = replace(self.context.request, request_id=uuid.uuid4().hex,
                                               step_id=uuid.uuid4().hex)
        except Exception as exc:
            interrupted = self._interrupted()
            if interrupted is not None:
                return interrupted
            code, error = _exception_error(exc)
            return self._finish(ResultStatus.FAILED, error=error, code=code)

    def _execute_turn(self, turn: ModelTurn, blocks, registry):
        tool_messages = []
        if self.model_rounds >= self.context.limits.max_model_rounds:
            return self._finish(ResultStatus.LIMITED, text=turn.content, turn=turn,
                                error="达到模型轮次上限，未执行后续工具。")
        # Never execute only a prefix when the returned batch exceeds a budget.
        searches = sum(call.name == "web_search" for call in turn.tool_calls)
        if self.tool_calls + len(turn.tool_calls) > self.context.limits.max_tool_calls:
            return self._finish(ResultStatus.LIMITED, text=turn.content, turn=turn, error="达到工具调用上限。")
        if self.search_calls + searches > self.context.limits.max_search_calls:
            return self._finish(ResultStatus.LIMITED, text=turn.content, turn=turn, error="达到联网搜索次数上限。")
        for call in turn.tool_calls:
            interrupted = self._interrupted(turn.content, turn)
            if interrupted is not None:
                return interrupted
            self.tool_calls += 1
            self.search_calls += call.name == "web_search"
            self._emit(RunEventKind.TOOL_STARTED, call=call)
            spec = registry.get(call.name)
            try:
                args = validate_tool_arguments(call)
                if spec is None:
                    raise ValueError("Tool not granted")
            except (ValueError, TypeError):
                output = ToolOutput("工具未授权或参数无效；请修正工具调用。", error=True)
            else:
                try:
                    output = spec.execute(args, ToolExecutionContext(self.current_request, call,
                                                                    self.cancelled, self._deadline,
                                                                    self.context.execution, self._budget,
                                                                    lambda state: self._emit(
                                                                        RunEventKind.TOOL_UPDATED, call=call,
                                                                        status=state)))
                    if not isinstance(output, ToolOutput) or not isinstance(output.text, str):
                        raise TypeError("Invalid executor output")
                except Exception:
                    # Native errors can contain credentials. Report a finite
                    # error, then let the model recover; never replay this call.
                    output = ToolOutput("工具执行失败；请调整方法或向用户说明。", error=True)
            output = bounded_tool_output(output, self.context.limits.tool_result_bytes)
            self._emit(RunEventKind.TOOL_FINISHED, call=call, status="failed" if output.error else "succeeded",
                       output=output)
            interrupted = self._interrupted(turn.content, turn)
            if interrupted is not None:
                return interrupted
            message = {"role": "tool", "tool_name": call.name, "content": output.text}
            if call.provider_call_id is not None:
                message["tool_call_id"] = call.provider_call_id
            tool_messages.append(message)
        # Previous complete tool blocks become optional; current user + most
        # recent complete protocol block stay pinned. No dangling tool messages.
        for i, block in enumerate(blocks):
            if any(m.get("tool_calls") for m in block.wire()):
                blocks[i] = replace(block, required=False)
        blocks.append(ProtocolBlock.create([turn.wire(), *tool_messages], required=True))
        return None
