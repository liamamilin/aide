"""Run system utilities without a shell. Tests inject a fake instead of this."""
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

MDFIND = "/usr/bin/mdfind"
OPEN = "/usr/bin/open"
OSASCRIPT = "/usr/bin/osascript"

Runner = Callable[[Sequence[str]], str]

# Spotlight can take several seconds when the machine is busy (for example while a model is running).
QUERY_TIMEOUT = 20.0


_cancelled: ContextVar[threading.Event | None] = ContextVar("desktop_cancelled", default=None)
_POLL_SECONDS = 0.1


@contextmanager
def cancellable(event: threading.Event):
    """Commands started in this block stop when the event is set (the user pressed stop)."""
    token = _cancelled.set(event)
    try:
        yield
    finally:
        _cancelled.reset(token)


def run_command(argv: Sequence[str], timeout: float = QUERY_TIMEOUT) -> str:
    """Return stdout; raise OSError for a failing, cancelled or timed-out command."""
    cancelled = _cancelled.get()
    process = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, _ = process.communicate(timeout=_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if cancelled is not None and cancelled.is_set():
                    raise OSError("command cancelled") from None
                if time.monotonic() >= deadline:
                    raise OSError("command timed out") from None
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        raise OSError(f"command failed with status {process.returncode}")
    return stdout


def run_script(script: str, args: Sequence[str] = (), *, runner: Runner = run_command, timeout: float = 15.0) -> str:
    """Run AppleScript with user text passed as arguments, never spliced into the script source."""
    if runner is run_command:
        return run_command([OSASCRIPT, "-e", script, *args], timeout=timeout)
    return runner([OSASCRIPT, "-e", script, *args])
