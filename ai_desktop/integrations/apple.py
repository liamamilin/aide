"""Create items in Apple Notes and Reminders. Text travels as script arguments, never in the source."""
import html
import logging
from dataclasses import dataclass

from ai_desktop.desktop.runner import Runner, run_command, run_script
from ai_desktop.integrations import reminders_eventkit

logger = logging.getLogger(__name__)

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

# `reminders whose completed is false` takes minutes on large lists; three bulk reads per list are faster.
_LIST_REMINDERS_SCRIPT = """on run argv
    set fieldSep to character id 31
    set recordSep to character id 30
    set out to ""
    tell application "Reminders"
        repeat with theList in lists
            set theIds to id of reminders of theList
            set theNames to name of reminders of theList
            set theDone to completed of reminders of theList
            repeat with i from 1 to (count of theIds)
                if not (item i of theDone) then
                    set out to out & (item i of theIds) & fieldSep & (item i of theNames) & recordSep
                end if
            end repeat
        end repeat
    end tell
    return out
end run"""

_REMINDER_TITLE_SCRIPT = """on run argv
    tell application "Reminders" to return name of reminder id (item 1 of argv)
end run"""

_COMPLETE_REMINDER_SCRIPT = """on run argv
    tell application "Reminders" to set completed of reminder id (item 1 of argv) to true
end run"""

_DELETE_REMINDER_SCRIPT = """on run argv
    tell application "Reminders" to delete reminder id (item 1 of argv)
end run"""

_APPEND_NOTE_SCRIPT = """on run argv
    tell application "Notes"
        set theNote to note id (item 1 of argv)
        set body of theNote to (body of theNote) & (item 2 of argv)
    end tell
end run"""

_NOTE_TITLE_SCRIPT = """on run argv
    tell application "Notes" to return name of note id (item 1 of argv)
end run"""

_DELETE_NOTE_SCRIPT = """on run argv
    tell application "Notes" to delete note id (item 1 of argv)
end run"""

MAX_LISTED = 50
NOTES_TIMEOUT = 60.0
REMINDERS_TIMEOUT = 120.0  # Reminders scripting needs ~10 s per property per 600 items
NOTE_ID_PREFIX = "x-coredata://"
REMINDER_ID_PREFIX = "x-apple-reminder://"


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
    for record in run_script(_LIST_NOTES_SCRIPT, runner=runner, timeout=NOTES_TIMEOUT).split(_RECORD_SEP):
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


def _eventkit_first(runner, native, fallback):
    """Use EventKit for the real system; an injected runner (tests) or an EventKit failure uses AppleScript."""
    if runner is run_command:
        try:
            return native()
        except OSError:
            logger.info("EventKit unavailable for Reminders; using AppleScript", exc_info=True)
    return fallback()


def list_reminders(*, runner: Runner = run_command) -> list[ReminderInfo]:
    """Every uncompleted reminder's id and title; notes and due dates are not read."""
    return _eventkit_first(runner, lambda: [ReminderInfo(*item) for item in reminders_eventkit.list_open()],
                           lambda: _list_reminders_script(runner))


def _list_reminders_script(runner) -> list[ReminderInfo]:
    found = []
    for record in run_script(_LIST_REMINDERS_SCRIPT, runner=runner, timeout=REMINDERS_TIMEOUT).split(_RECORD_SEP):
        fields = record.strip("\n").split(_FIELD_SEP)
        if len(fields) == 2 and fields[0]:
            found.append(ReminderInfo(fields[0], fields[1]))
    return found


def _check_id(value, prefix, what) -> str:
    if (not isinstance(value, str) or not value.startswith(prefix) or len(value) > 500
            or any(ord(ch) < 32 for ch in value)):
        raise ValueError(f"Invalid {what} id")
    return value


def check_note_id(note_id) -> str:
    return _check_id(note_id, NOTE_ID_PREFIX, "note")


def check_reminder_id(reminder_id) -> str:
    return _check_id(reminder_id, REMINDER_ID_PREFIX, "reminder")


def reminder_title(reminder_id: str, *, runner: Runner = run_command) -> str:
    reminder_id = check_reminder_id(reminder_id)
    return _eventkit_first(runner, lambda: reminders_eventkit.title_of(reminder_id),
                           lambda: run_script(_REMINDER_TITLE_SCRIPT, [reminder_id], runner=runner).strip())


def complete_reminder(reminder_id: str, *, runner: Runner = run_command) -> None:
    reminder_id = check_reminder_id(reminder_id)
    _eventkit_first(runner, lambda: reminders_eventkit.complete(reminder_id),
                    lambda: run_script(_COMPLETE_REMINDER_SCRIPT, [reminder_id], runner=runner))


def delete_reminder(reminder_id: str, *, runner: Runner = run_command) -> None:
    """Reminders has no Recently Deleted folder: this cannot be undone."""
    reminder_id = check_reminder_id(reminder_id)
    _eventkit_first(runner, lambda: reminders_eventkit.delete(reminder_id),
                    lambda: run_script(_DELETE_REMINDER_SCRIPT, [reminder_id], runner=runner))


def note_title(note_id: str, *, runner: Runner = run_command) -> str:
    return run_script(_NOTE_TITLE_SCRIPT, [check_note_id(note_id)], runner=runner).strip()


def append_html(text: str) -> str:
    return "".join(f"<div>{html.escape(line) or '<br>'}</div>" for line in text.splitlines())


def append_to_note(note_id: str, text: str, *, runner: Runner = run_command) -> None:
    """Add lines at the end of an existing note; nothing already in it is removed or reordered."""
    text = _text(text, MAX_BODY, "note text", required=True)
    run_script(_APPEND_NOTE_SCRIPT, [check_note_id(note_id), append_html(text)], runner=runner)


def delete_note(note_id: str, *, runner: Runner = run_command) -> None:
    """Moves the note to Recently Deleted, where Notes keeps it for 30 days."""
    run_script(_DELETE_NOTE_SCRIPT, [check_note_id(note_id)], runner=runner)
