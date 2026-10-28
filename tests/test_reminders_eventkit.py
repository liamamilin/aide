import pytest

from ai_desktop.desktop.runner import run_command
from ai_desktop.integrations import apple, reminders_eventkit


def test_identifiers_must_use_the_reminder_scheme():
    with pytest.raises(ValueError):
        reminders_eventkit._identifier("x-coredata://note")
    assert reminders_eventkit._identifier("x-apple-reminder://ABC") == "ABC"


def test_eventkit_is_used_for_the_real_runner_and_script_is_the_fallback():
    calls = []

    def native():
        calls.append("native")
        return "native"

    def fallback():
        calls.append("script")
        return "script"

    assert apple._eventkit_first(run_command, native, fallback) == "native"
    assert apple._eventkit_first(lambda argv: "", native, fallback) == "script"  # an injected runner means a test
    assert calls == ["native", "script"]


def test_eventkit_failure_falls_back_but_bad_input_is_not_hidden():
    def denied():
        raise OSError("Reminders access was not granted")

    def missing():
        raise ValueError("Reminder not found")

    assert apple._eventkit_first(run_command, denied, lambda: "script") == "script"
    with pytest.raises(ValueError):
        apple._eventkit_first(run_command, missing, lambda: "script")


def test_tests_never_reach_the_real_reminders(monkeypatch):
    monkeypatch.setattr(reminders_eventkit, "_store_with_access", lambda: pytest.fail("real EventKit used"))
    out = apple.list_reminders(runner=lambda argv: "r1\x1f交周报\x1e")
    assert out == [apple.ReminderInfo("r1", "交周报")]
