"""Run system utilities without a shell. Tests inject a fake instead of this."""
import subprocess
from collections.abc import Callable, Sequence

MDFIND = "/usr/bin/mdfind"
OPEN = "/usr/bin/open"
OSASCRIPT = "/usr/bin/osascript"

Runner = Callable[[Sequence[str]], str]

# Spotlight can take several seconds when the machine is busy (for example while a model is running).
QUERY_TIMEOUT = 20.0


def run_command(argv: Sequence[str], timeout: float = QUERY_TIMEOUT) -> str:
    """Return stdout; raise OSError for a failing or timed-out command."""
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError("command timed out") from exc
    if result.returncode != 0:
        raise OSError(f"command failed with status {result.returncode}")
    return result.stdout


def run_script(script: str, args: Sequence[str] = (), *, runner: Runner = run_command, timeout: float = 15.0) -> str:
    """Run AppleScript with user text passed as arguments, never spliced into the script source."""
    if runner is run_command:
        return run_command([OSASCRIPT, "-e", script, *args], timeout=timeout)
    return runner([OSASCRIPT, "-e", script, *args])
