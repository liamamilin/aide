"""Bounded Bash execution; no automatic retries and no inherited credentials."""
import json
import os
import selectors
import signal
import subprocess
import time
from contextlib import nullcontext
from dataclasses import dataclass

from ai_desktop.llm.run_types import ToolOutput
from ai_desktop.services.bash_policy import CommandDisposition, classify_command
from ai_desktop.services.command_confirmation import CommandConfirmation, ConfirmationBroker, ConfirmationOutcome

_CAPTURE_BYTES = 32 * 1024  # each pipe; bounded in memory, no raw output file
_OUTPUT_LIMIT = 1024 * 1024  # aggregate streamed bytes, then stop process group


@dataclass(frozen=True)
class BashResult:
    exit_code: int | None = None
    stdout: bytes = b''
    stderr: bytes = b''
    duration: float = 0
    truncated: bool = False
    error_type: str = ''
    execution_mode: str = ''

    def tool_output(self):
        # Keep structured JSON within the runtime's standard 4 KiB envelope.
        def excerpt(data):
            return data[:1024].decode('utf-8', errors='replace')
        record = {'exit_code': self.exit_code, 'stdout': excerpt(self.stdout), 'stderr': excerpt(self.stderr),
                  'duration': round(self.duration, 3), 'truncated': self.truncated or
                  len(self.stdout) > 1024 or len(self.stderr) > 1024,
                  'error_type': self.error_type, 'execution_mode': self.execution_mode}
        text = json.dumps(record, ensure_ascii=False)
        while len(text.encode()) > 4096:
            record['stdout'] = record['stdout'][:len(record['stdout']) // 2]
            record['stderr'] = record['stderr'][:len(record['stderr']) // 2]
            record['truncated'] = True
            text = json.dumps(record, ensure_ascii=False)
        return ToolOutput(text, bool(self.error_type) or self.exit_code not in (None, 0))


def _terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    # Also kill remaining children after the group leader exits. Group lifetime
    # can outlast its parent; detached sessions are outside this guarantee.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


def run_command(argv, snapshot, context, timeout, *, execution_mode):
    start = time.monotonic()
    if context.cancelled.is_set():
        return BashResult(error_type='cancelled', execution_mode=execution_mode)
    deadline = context.active_deadline
    if deadline is not None and start >= deadline:
        return BashResult(error_type='active_timeout', execution_mode=execution_mode)
    if not snapshot.valid():
        return BashResult(error_type='workspace_changed', execution_mode=execution_mode)
    try:
        process = subprocess.Popen(argv, cwd=snapshot.workspace, env=snapshot.environment(),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, start_new_session=True)
    except OSError:
        return BashResult(error_type='start_failed', execution_mode=execution_mode)
    selector = selectors.DefaultSelector()
    buffers = {'stdout':bytearray(), 'stderr':bytearray()}
    total, error, truncated = 0, '', False
    command_deadline = start + timeout
    exited_at = None
    try:
        for name, pipe in (('stdout', process.stdout), ('stderr', process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            deadline = context.active_deadline
            if context.cancelled.is_set():
                error = 'cancelled'
            elif deadline is not None and now >= deadline:
                error = 'active_timeout'
            elif now >= command_deadline:
                error = 'timeout'
            elif process.poll() is not None:
                exited_at = exited_at or now
                if selector.get_map() and now - exited_at >= 0.2:
                    error = 'unfinished_children'
            if error:
                break
            for key, _ in selector.select(0.02):
                try:
                    data = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                total += len(data)
                buffer = buffers[key.data]
                available = max(0, _CAPTURE_BYTES - len(buffer))
                buffer.extend(data[:available])
                truncated |= len(data) > available
                if total >= _OUTPUT_LIMIT:
                    error, truncated = 'output_limit', True
                    break
            if error:
                break
        return_code = process.poll()
    except Exception:
        error, return_code = 'io_failed', process.poll()
    finally:
        _terminate_group(process)
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return BashResult(return_code if return_code is not None else process.returncode,
                      bytes(buffers['stdout']), bytes(buffers['stderr']), time.monotonic() - start,
                      truncated, error, execution_mode)


class BashExecutor:
    def __init__(self, confirmations: ConfirmationBroker | None = None):
        self.confirmations = confirmations or ConfirmationBroker()

    def __call__(self, args, context):
        snapshot = context.execution
        if snapshot is None:
            return BashResult(error_type='workspace_required').tool_output()
        if context.cancelled.is_set():
            return BashResult(error_type='cancelled').tool_output()
        command, timeout = args['command'], args.get('timeout', 30)
        decision = classify_command(command, snapshot)
        if decision.disposition == CommandDisposition.INVALID:
            return BashResult(stderr=decision.reason.encode(), error_type='invalid_command').tool_output()
        if context.active_deadline is not None and time.monotonic() >= context.active_deadline:
            return BashResult(error_type="active_timeout").tool_output()
        if decision.disposition == CommandDisposition.AUTO:
            argv, mode = decision.argv, 'argv'
        else:
            if getattr(context, 'on_state', None) is not None:
                context.on_state('waiting_confirmation')
            request = CommandConfirmation.create(context, command, timeout, decision.reason,
                                                 clock=self.confirmations.clock)
            pause = context.budget.pause() if context.budget is not None else nullcontext()
            with pause:
                outcome = self.confirmations.wait(request, context.cancelled)
            if outcome != ConfirmationOutcome.APPROVED:
                return BashResult(error_type='confirmation_' + outcome.value).tool_output()
            # A cancelled task or changed workspace is rechecked immediately
            # before spawn; approval never bypasses lifecycle checks.
            argv, mode = ('/bin/bash', '--noprofile', '--norc', '-c', command), 'bash'
        if getattr(context, 'on_state', None) is not None:
            context.on_state('executing')
        return run_command(argv, snapshot, context, timeout, execution_mode=mode).tool_output()


def probe_execution_runtime():
    """Exercise frozen imports and a fixed read-only fixture, never user commands."""
    import tempfile
    import threading
    from pathlib import Path
    from types import SimpleNamespace

    from ai_desktop.services.execution_context import ExecutionSnapshot

    with tempfile.TemporaryDirectory(prefix='aide-execution-probe-') as directory:
        snapshot = ExecutionSnapshot.create(directory)
        (Path(directory) / 'fixture.txt').write_text('execution-ok')
        context = SimpleNamespace(execution=snapshot, cancelled=threading.Event(),
                                  active_deadline=time.monotonic()+3, budget=None)
        output = BashExecutor()({'command':'cat fixture.txt','timeout':3},context)
        result = json.loads(output.text)
        if output.error or result['stdout'] != 'execution-ok' or result['execution_mode'] != 'argv':
            raise RuntimeError('Execution runtime probe failed')
    return {'engine':'bash','status':'ready','automatic_mode':'fixed_argv'}
