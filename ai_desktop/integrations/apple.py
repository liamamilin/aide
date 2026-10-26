"""Create items in Apple Notes and Reminders. Text travels as script arguments, never in the source."""
import html
from dataclasses import dataclass

from ai_desktop.desktop.runner import Runner, run_command, run_script

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
MAX_TITLE = 200
MAX_BODY = 8000

_NOTE_SCRIPT = """on run argv
    tell application "Notes"
        set created to make new note at default account with properties {body:(item 1 of argv)}
        return id of created
    end tell
end run"""

_REMINDER_SCRIPT = """on run argv
    tell application "Reminders"
        set created to make new reminder with properties {name:(item 1 of argv), body:(item 2 of argv)}
        return id of created
    end tell
end run"""

# One bulk read per property; age is seconds since the last change (no locale-dependent dates).
_LIST_NOTES_SCRIPT = """on run argv
    tell application "Notes"
        set noteIds to id of every note
        set noteNames to name of every note
        set noteDates to modification date of every note
    end tell
    set nowDate to current date
    set fieldSep to character id 31
    set recordSep to character id 30
    set out to ""
    repeat with i from 1 to (count of noteIds)
        if i > 2000 then exit repeat
        set age to (nowDate - (item i of noteDates)) as integer
        set out to out & (item i of noteIds) & fieldSep & (item i of noteNames) & fieldSep & age & recordSep
    end repeat
    return out
end run"""

_LIST_REMINDERS_SCRIPT = """on run argv
    tell application "Reminders"
        set pending to (reminders whose completed is false)
        set reminderIds to id of pending
        set reminderNames to name of pending
    end tell
    set fieldSep to character id 31
    set recordSep to character id 30
    set out to ""
    repeat with i from 1 to (count of reminderIds)
        if i > 2000 then exit repeat
        set out to out & (item i of reminderIds) & fieldSep & (item i of reminderNames) & recordSep
    end repeat
    return out
end run"""

_NOTE_TITLE_SCRIPT = """on run argv
    tell application "Notes" to return name of note id (item 1 of argv)
end run"""

_DELETE_NOTE_SCRIPT = """on run argv
    tell application "Notes" to delete note id (item 1 of argv)
end run"""

MAX_LISTED = 50
NOTE_ID_PREFIX = "x-coredata://"


def _text(value, limit, what, *, required):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit or "\x00" in value:
        raise ValueError(f"Invalid {what}")
    return value.strip()


def note_html(title: str, body: str) -> str:
    """Notes stores HTML; escape everything so the text cannot add markup."""
    lines = "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in body.splitlines())
    return f"<div><b>{html.escape(title)}</b></div>{lines}"


def create_note(title: str, body: str = "", *, runner: Runner = run_command) -> str:
    """Return the new note's id so a later call can refer to exactly this note."""
    title = _text(title, MAX_TITLE, "note title", required=True)
    body = _text(body, MAX_BODY, "note body", required=False)
    return run_script(_NOTE_SCRIPT, [note_html(title, body)], runner=runner).strip()


def create_reminder(title: str, notes: str = "", *, runner: Runner = run_command) -> str:
    title = _text(title, MAX_TITLE, "reminder title", required=True)
    notes = _text(notes, MAX_BODY, "reminder notes", required=False)
    return run_script(_REMINDER_SCRIPT, [title, notes], runner=runner).strip()


@dataclass(frozen=True)
class NoteInfo:
    id: str
    title: str
    age_seconds: int


def list_notes(*, runner: Runner = run_command) -> list[NoteInfo]:
    """Every note's id, title and age, newest first. Bodies are never read."""
    found = []
    for record in run_script(_LIST_NOTES_SCRIPT, runner=runner).split(_RECORD_SEP):
        fields = record.strip("\n").split(_FIELD_SEP)
        if len(fields) != 3 or not fields[0].startswith(NOTE_ID_PREFIX):
            continue
        try:
            age = max(int(float(fields[2])), 0)
        except ValueError:
            continue
        found.append(NoteInfo(fields[0], fields[1], age))
    return sorted(found, key=lambda note: note.age_seconds)


@dataclass(frozen=True)
class ReminderInfo:
    id: str
    title: str


def list_reminders(*, runner: Runner = run_command) -> list[ReminderInfo]:
    """Every uncompleted reminder's id and title; notes and due dates are not read."""
    found = []
    for record in run_script(_LIST_REMINDERS_SCRIPT, runner=runner, timeout=30.0).split(_RECORD_SEP):
        fields = record.strip("\n").split(_FIELD_SEP)
        if len(fields) == 2 and fields[0]:
            found.append(ReminderInfo(fields[0], fields[1]))
    return found


def check_note_id(note_id) -> str:
    if (not isinstance(note_id, str) or not note_id.startswith(NOTE_ID_PREFIX) or len(note_id) > 500
            or any(ord(ch) < 32 for ch in note_id)):
        raise ValueError("Invalid note id")
    return note_id


def note_title(note_id: str, *, runner: Runner = run_command) -> str:
    return run_script(_NOTE_TITLE_SCRIPT, [check_note_id(note_id)], runner=runner).strip()


def delete_note(note_id: str, *, runner: Runner = run_command) -> None:
    """Moves the note to Recently Deleted, where Notes keeps it for 30 days."""
    run_script(_DELETE_NOTE_SCRIPT, [check_note_id(note_id)], runner=runner)
