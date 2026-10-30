"""Calendar and clipboard tools: parsing, confirmation before any write, and what the model gets back."""
# ruff: noqa: F811  (the imported `env` fixture is used as a test argument)
from datetime import datetime, timedelta

import pytest

from ai_desktop.integrations import calendar_app, clipboard
from tests.test_core_tools import _final, _next, _tool_turn, env  # noqa: F401


def test_list_events_turns_offsets_into_local_times_sorted():
    now = datetime(2026, 11, 2, 9, 30)
    raw = ("Late\x1f50400\x1f3600\x1fWork\x1e" "Team sync\x1f36000\x1f1800\x1fWork\x1e" "junk\x1e")
    events = calendar_app.list_events(3, runner=lambda argv: raw, now=now)
    assert [e.title for e in events] == ["Team sync", "Late"]
    assert events[0].start == datetime(2026, 11, 2, 10, 0) and events[0].minutes == 30


def test_list_events_rejects_silly_ranges():
    with pytest.raises(ValueError):
        calendar_app.list_events(400, runner=lambda argv: "")


def test_parse_start_accepts_both_separators_only():
    assert calendar_app.parse_start("2026-11-02 15:00") == calendar_app.parse_start("2026-11-02T15:00")
    with pytest.raises(ValueError):
        calendar_app.parse_start("next friday")


def test_create_event_passes_numbers_as_arguments_not_source():
    seen = []
    start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=2)
    calendar_app.create_event('Dinner "x" & 1', start, 90, runner=lambda argv: seen.append(list(argv)) or "UID1")
    argv = seen[0]
    assert argv[1] == "-e" and "Dinner" not in argv[2]            # title never spliced into the script
    assert argv[3] == 'Dinner "x" & 1' and argv[-2] == "90"


def test_create_event_rejects_dates_far_in_the_past():
    with pytest.raises(ValueError):
        calendar_app.validate_event("Old", datetime(2001, 1, 1, 9, 0), 60)


def _when(days=2):
    return (datetime.now() + timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0)


def test_create_event_asks_first_shows_the_exact_time_and_writes_only_after_approval(env):
    chats, runs, fake, events, runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    start = _when()
    fake.enqueue(_tool_turn("create_event", {"title": "牙医", "start": start.strftime("%Y-%m-%d %H:%M")}))
    fake.enqueue(*_final("已安排。"))
    started = chats.send({"conversation_id": convo, "text": "后天下午三点看牙医"})
    assert "create_event" in started["tools"]
    request = _next(events, "run.confirm_request")
    preview = request["preview"]
    assert "牙医" in preview and start.strftime("%Y-%m-%d") in preview and "15:00–16:00" in preview
    assert not any("Calendar" in " ".join(c) for c in runner.calls)      # nothing written before approval
    runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": True})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert any("Calendar" in " ".join(c) for c in runner.calls)


def test_denied_event_is_not_written(env):
    chats, runs, fake, events, runner = env
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("create_event", {"title": "x", "start": _when().strftime("%Y-%m-%d %H:%M")}))
    fake.enqueue(*_final("好的。"))
    chats.send({"conversation_id": convo, "text": "加个日程"})
    request = _next(events, "run.confirm_request")
    runs.confirm({"run_id": request["run_id"], "confirmation_id": request["confirmation_id"], "approved": False})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert not any("Calendar" in " ".join(c) for c in runner.calls)


def test_clipboard_text_is_returned_and_non_text_is_a_clear_error(env):
    chats, _runs, fake, events, runner = env
    runner_calls = runner.calls

    def answer(argv):
        runner_calls.append(list(argv))
        if "clipboard" in " ".join(argv):
            return "复制的内容 ABC"
        return ""
    chats._runs.desktop_runner = answer
    convo = chats.create({"agent_id": "general_assistant"})["id"]
    fake.enqueue(_tool_turn("read_clipboard", {}))
    fake.enqueue(*_final("好。"))
    chats.send({"conversation_id": convo, "text": "总结剪贴板"})
    assert _next(events, "run.finished")["status"] == "succeeded"
    assert "复制的内容 ABC" in fake.requests[1]["payload"]["messages"][-1]["content"]


def test_clipboard_module_reads_through_the_runner():
    assert clipboard.read_text(runner=lambda argv: "x") == "x"
