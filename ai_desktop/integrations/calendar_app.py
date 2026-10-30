"""Calendar events through AppleScript. Text and numbers travel as script arguments, never in the source.

Only timed events can be created (an all-day event needs different end-date rules that are not verified).
Repeating events are listed by Calendar under their first date only, so a list can miss later occurrences.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from ai_desktop.desktop.runner import Runner, run_command, run_script

MAX_TITLE = 200
MAX_NOTES = 2000
MAX_EVENTS = 200
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

_LIST_SCRIPT = """on run argv
    set d0 to current date
    set time of d0 to 0
    set d0 to d0 + ((item 1 of argv) as integer) * days
    set d1 to d0 + ((item 2 of argv) as integer) * days
    set fieldSep to character id 31
    set recordSep to character id 30
    set out to ""
    tell application "Calendar"
        repeat with c in calendars
            set found to (every event of c whose start date is greater than or equal to d0 ¬
                and start date is less than d1)
            repeat with e in found
                set out to out & (summary of e) & fieldSep & (((start date of e) - d0) as integer) & fieldSep & ¬
                    (((end date of e) - (start date of e)) as integer) & fieldSep & (name of c) & recordSep
            end repeat
        end repeat
    end tell
    return out
end run"""

_CREATE_SCRIPT = """on run argv
    set d to current date
    set day of d to 1
    set year of d to (item 2 of argv) as integer
    set month of d to (item 3 of argv) as integer
    set day of d to (item 4 of argv) as integer
    set hours of d to (item 5 of argv) as integer
    set minutes of d to (item 6 of argv) as integer
    set seconds of d to 0
    set endDate to d + ((item 7 of argv) as integer) * minutes
    tell application "Calendar"
        set cal to first calendar whose writable is true
        set created to make new event at end of events of cal with properties ¬
            {summary:(item 1 of argv), start date:d, end date:endDate, description:(item 8 of argv)}
        return uid of created
    end tell
end run"""


@dataclass(frozen=True)
class EventInfo:
    title: str
    start: datetime
    minutes: int
    calendar: str


def _local_midnight(day_offset: int, now: datetime) -> datetime:
    return (now + timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)


def list_events(days_ahead: int = 7, *, start_offset: int = 0, runner: Runner = run_command,
                now: datetime | None = None) -> list[EventInfo]:
    if not 1 <= days_ahead <= 31 or not -1 <= start_offset <= 31:
        raise ValueError("date range out of bounds")
    now = now or datetime.now()
    raw = run_script(_LIST_SCRIPT, [str(start_offset), str(days_ahead)], runner=runner, timeout=40.0)
    base = _local_midnight(start_offset, now)
    events = []
    for record in raw.split(_RECORD_SEP):
        fields = record.strip("\n").split(_FIELD_SEP)
        if len(fields) != 4:
            continue
        try:
            events.append(EventInfo(fields[0], base + timedelta(seconds=int(float(fields[1]))),
                                    int(float(fields[2])) // 60, fields[3]))
        except ValueError:
            continue
    events.sort(key=lambda event: event.start)
    return events[:MAX_EVENTS]


def parse_start(text: str) -> datetime:
    """`YYYY-MM-DD HH:MM` or `YYYY-MM-DDTHH:MM`, local time, to the minute."""
    if not isinstance(text, str):
        raise ValueError("start must be a string")
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    raise ValueError("start must look like 2026-11-02 15:00")


def validate_event(title: str, start: datetime, minutes: int, notes: str = "") -> str:
    """The cleaned title, or ValueError. Checked before the person is asked to confirm."""
    title = (title or "").strip()
    if not title or len(title) > MAX_TITLE or "\x00" in title:
        raise ValueError("invalid event title")
    if not 5 <= minutes <= 1440:
        raise ValueError("duration out of bounds")
    if not isinstance(notes, str) or len(notes) > MAX_NOTES or "\x00" in notes:
        raise ValueError("invalid notes")
    if not time.time() - 86400 <= start.timestamp() <= time.time() + 5 * 366 * 86400:
        raise ValueError("start is outside the supported range")
    return title


def create_event(title: str, start: datetime, minutes: int, notes: str = "", *,
                 runner: Runner = run_command) -> str:
    title = validate_event(title, start, minutes, notes)
    args = [title, str(start.year), str(start.month), str(start.day), str(start.hour), str(start.minute),
            str(minutes), notes]
    return run_script(_CREATE_SCRIPT, args, runner=runner).strip()
