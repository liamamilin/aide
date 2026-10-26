"""Create items in Apple Notes and Reminders. Text travels as script arguments, never in the source."""
import html

from ai_desktop.desktop.runner import Runner, run_command, run_script

MAX_TITLE = 200
MAX_BODY = 8000

_NOTE_SCRIPT = """on run argv
    tell application "Notes"
        make new note at default account with properties {body:(item 1 of argv)}
    end tell
end run"""

_REMINDER_SCRIPT = """on run argv
    tell application "Reminders"
        make new reminder with properties {name:(item 1 of argv), body:(item 2 of argv)}
    end tell
end run"""


def _text(value, limit, what, *, required):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid {what}")
    return value.strip()


def note_html(title: str, body: str) -> str:
    """Notes stores HTML; escape everything so the text cannot add markup."""
    lines = "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in body.splitlines())
    return f"<div><b>{html.escape(title)}</b></div>{lines}"


def create_note(title: str, body: str = "", *, runner: Runner = run_command) -> None:
    title = _text(title, MAX_TITLE, "note title", required=True)
    body = _text(body, MAX_BODY, "note body", required=False)
    run_script(_NOTE_SCRIPT, [note_html(title, body)], runner=runner)


def create_reminder(title: str, notes: str = "", *, runner: Runner = run_command) -> None:
    title = _text(title, MAX_TITLE, "reminder title", required=True)
    notes = _text(notes, MAX_BODY, "reminder notes", required=False)
    run_script(_REMINDER_SCRIPT, [title, notes], runner=runner)
