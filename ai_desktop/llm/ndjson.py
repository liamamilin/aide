"""Incremental NDJSON decoding of Ollama chat streams (transport-independent, no Qt)."""
import json
from collections.abc import Iterator

from ai_desktop.llm.events import ErrorCode, EventKind, RequestContext, StreamEvent
from ai_desktop.llm.ollama_protocol import StreamProtocolError, TurnAssembler


class NDJSONDecoder:
    """Buffer bytes until a whole UTF-8 JSON line is available, including EOF."""

    def __init__(self, request_id: str | RequestContext):
        self.request = request_id if isinstance(request_id, RequestContext) else None
        self.request_id = self.request.request_id if self.request else request_id
        self._buffer = bytearray()
        self.terminal = False
        self.assembler = TurnAssembler()
        self._seq = 0

    def _event(self, kind, text="", code=None, turn=None):
        self._seq += 1
        req = self.request
        return StreamEvent(self.request_id, kind, text, code, turn,
                           req.run_id if req else "", req.step_id if req else "",
                           req.conversation_id if req else 0, self._seq)

    def feed(self, data: bytes, *, final: bool = False) -> Iterator[StreamEvent]:
        if self.terminal:
            return
        self._buffer.extend(data)
        if len(self._buffer) > TurnAssembler.MAX_BYTES:
            raise StreamProtocolError("NDJSON buffer too large")
        while not self.terminal:
            end = self._buffer.find(b"\n")
            if end < 0:
                if not final or not self._buffer:
                    break
                end = len(self._buffer)
            line = bytes(self._buffer[:end]).strip()
            del self._buffer[:end + 1]
            if not line:
                continue
            value = json.loads(line.decode("utf-8"))
            message, error = self.assembler.accept(value)
            if error:
                self.terminal = True
                yield self._event(EventKind.ERROR, error, ErrorCode.SERVER)
                break
            for field, kind in (("thinking", EventKind.THINKING), ("content", EventKind.CONTENT)):
                if message.get(field):
                    yield self._event(kind, message[field])
            if value.get("done"):
                self.terminal = True
                turn = self.assembler.turn
                kind = EventKind.LIMITED if turn.done_reason == "length" else EventKind.COMPLETE
                yield self._event(kind, turn=turn)
        if self.terminal:
            self._buffer.clear()
        elif final:
            raise StreamProtocolError("Missing completion event")
