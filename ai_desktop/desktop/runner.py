"""Run system utilities without a shell. Tests inject a fake instead of this."""
import subprocess
from collections.abc import Callable, Sequence

MDFIND = "/usr/bin/mdfind"
OPEN = "/usr/bin/open"

Runner = Callable[[Sequence[str]], str]


def run_command(argv: Sequence[str], timeout: float = 5.0) -> str:
    """Return stdout; raise OSError for a failing or timed-out command."""
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError("command timed out") from exc
    if result.returncode != 0:
        raise OSError(f"command failed with status {result.returncode}")
    return result.stdout
