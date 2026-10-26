"""Reminders through EventKit: milliseconds instead of the ~30 s AppleScript needs on a large list.

Every function raises OSError when EventKit is unavailable or access is not granted, so callers can fall back
to AppleScript. Identifiers are returned as `x-apple-reminder://<id>` to match the AppleScript ids.
"""
import threading

ID_PREFIX = "x-apple-reminder://"
ACCESS_TIMEOUT = 60.0  # the first request shows a system permission prompt
FETCH_TIMEOUT = 15.0
_AUTHORIZED = {2, 3}  # EKAuthorizationStatusAuthorized (legacy) and FullAccess
_store = None
_lock = threading.Lock()


def _framework():
    try:
        import EventKit
    except ImportError as exc:
        raise OSError("EventKit is not available") from exc
    return EventKit


def _store_with_access():
    global _store
    EventKit = _framework()
    with _lock:
        if _store is None:
            _store = EventKit.EKEventStore.alloc().init()
        status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeReminder)
        if status in _AUTHORIZED:
            return _store
        if status != 0:
            raise OSError("Reminders access was not granted")  # denied or restricted
        answered, granted = threading.Event(), []

        def done(ok, error):
            granted.append(bool(ok))
            answered.set()

        request = getattr(_store, "requestFullAccessToRemindersWithCompletion_", None)
        if request is not None:
            request(done)
        else:
            _store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeReminder, done)
        if not answered.wait(ACCESS_TIMEOUT) or not granted[0]:
            raise OSError("Reminders access was not granted")
        return _store


def _identifier(reminder_id: str) -> str:
    if not isinstance(reminder_id, str) or not reminder_id.startswith(ID_PREFIX):
        raise ValueError("Invalid reminder id")
    return reminder_id[len(ID_PREFIX):]


def _find(store, reminder_id: str):
    item = store.calendarItemWithIdentifier_(_identifier(reminder_id))
    if item is None:
        raise ValueError("Reminder not found")
    return item


def list_open() -> list[tuple[str, str]]:
    """(id, title) for every uncompleted reminder."""
    store = _store_with_access()
    predicate = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)
    answered, found = threading.Event(), []

    def done(reminders):
        found.extend(reminders or [])
        answered.set()

    store.fetchRemindersMatchingPredicate_completion_(predicate, done)
    if not answered.wait(FETCH_TIMEOUT):
        raise OSError("Reading reminders timed out")
    return [(ID_PREFIX + str(item.calendarItemIdentifier()), str(item.title() or "")) for item in found]


def title_of(reminder_id: str) -> str:
    return str(_find(_store_with_access(), reminder_id).title() or "")


def complete(reminder_id: str) -> None:
    store = _store_with_access()
    item = _find(store, reminder_id)
    item.setCompleted_(True)
    ok, error = store.saveReminder_commit_error_(item, True, None)
    if not ok:
        raise OSError("Could not complete the reminder")


def delete(reminder_id: str) -> None:
    store = _store_with_access()
    ok, error = store.removeReminder_commit_error_(_find(store, reminder_id), True, None)
    if not ok:
        raise OSError("Could not delete the reminder")
