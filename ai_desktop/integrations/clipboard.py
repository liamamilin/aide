"""Read the plain-text clipboard with AppleScript's `the clipboard` (no Automation permission needed)."""
from ai_desktop.desktop.runner import Runner, run_command, run_script

_READ_SCRIPT = """on run argv
    return the clipboard as text
end run"""


def read_text(*, runner: Runner = run_command) -> str:
    """The clipboard as text; raises OSError when it holds something other than text."""
    return run_script(_READ_SCRIPT, runner=runner)
