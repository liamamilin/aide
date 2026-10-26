"""The scenario evaluation's scoring and fixtures, driven by scripted model replies (no Ollama)."""
import pytest

from ai_desktop.diagnostics import scenario_eval as ev

BY_ID = {scenario.id: scenario for scenario in ev.SCENARIOS}


def call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


class Scripted:
    """Replays assistant messages in order and records each request."""

    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, payload):
        self.requests.append(payload)
        return {"message": self.messages.pop(0)}


def say(text):
    return {"content": text}


def use(*calls, content=""):
    return {"content": content, "tool_calls": list(calls)}


def run(scenario_id, *replies):
    scenario = BY_ID[scenario_id]
    chat = Scripted(*replies)
    outcome = ev.run_scenario(scenario, "m", chat, options={})
    return ev.score(scenario, outcome), outcome, chat


def test_open_wechat_passes_with_the_right_path_and_fails_with_a_wrong_one():
    ok, outcome, _ = run("open_wechat", use(call("find_apps", query="微信")),
                         use(call("open_path", path="/Applications/WeChat.app")), say("已打开微信"))
    assert ok == "" and [c.name for c in outcome.calls] == ["find_apps", "open_path"]
    bad, *_ = run("open_wechat", use(call("open_path", path="/Applications/Safari.app")), say("好"))
    assert bad


def test_search_fixtures_are_literal_substring_matches():
    fixtures = ev.Fixtures()
    assert fixtures.run("find_files", {"query": "产品方案"})["results"]
    assert fixtures.run("find_files", {"query": "上周写的产品方案"})["results"] == []
    assert fixtures.run("find_apps", {"query": "备忘录"})["results"][0]["name"] == "Notes"


def test_overspecific_document_query_fails_s1():
    reason, *_ = run("s1_find_doc", use(call("find_files", query="上周写的产品方案")), say("没找到"))
    assert "查询词" in reason
    ok, *_ = run("s1_find_doc", use(call("find_files", query="产品方案")), say("找到：产品方案 V2.docx"))
    assert ok == ""


def test_ambiguous_notes_may_ask_or_open_notes_but_never_voice_memos():
    asks = (use(call("find_apps", query="备忘录")), say("找到备忘录和语音备忘录，打开哪个？"))
    assert run("open_ambiguous", *asks)[0] == ""
    notes = use(call("open_path", path="/System/Applications/Notes.app"))
    assert run("open_ambiguous", notes, say("ok"))[0] == ""
    voice = use(call("open_path", path="/System/Applications/VoiceMemos.app"))
    assert "语音" in run("open_ambiguous", voice, say("ok"))[0]


def test_windows_need_both_apps_in_opposite_halves():
    good = (use(call("arrange_window", app="Safari", layout="left_half"),
                call("arrange_window", app="Notes", layout="right_half")), say("好了"))
    assert run("s5_windows", *good)[0] == ""
    same = (use(call("arrange_window", app="Safari", layout="left_half"),
                call("arrange_window", app="Notes", layout="left_half")), say("好了"))
    assert run("s5_windows", *same)[0]


def test_unsupported_requests_must_not_be_faked():
    assert run("f2_cancel_note", say("我目前无法修改或删除已有笔记。"))[0] == ""
    assert run("f2_cancel_note", say("已为您取消了那条备忘录。"))[0]
    assert run("f1_count_notes", say("我还不能读取备忘录。"))[0] == ""
    assert run("f1_count_notes", say("您的备忘录共有 12 条。"))[0]
    assert run("s4_organize", say("目前不能移动文件。"))[0] == ""
    assert run("s4_organize", say("已整理完成。"))[0]


def test_failed_open_must_not_be_reported_as_success():
    failing = (use(call("open_path", path="/Applications/WeChat.app")),)
    assert run("open_fails", *failing, say("没能打开微信。"))[0] == ""
    assert run("open_fails", *failing, say("已为您打开微信。"))[0]


@pytest.mark.parametrize("scenario_id", ["neg_chat", "neg_knowledge", "neg_translate"])
def test_negative_scenarios_fail_on_any_tool_call(scenario_id):
    assert run(scenario_id, say("你好"))[0] == ""
    assert run(scenario_id, use(call("find_files", query="x")), say("好"))[0]


def test_request_carries_prior_turns_the_real_prompt_and_all_native_tools():
    _, _, chat = run("f2_cancel_note", say("做不到"))
    request = chat.requests[0]
    roles = [m["role"] for m in request["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "[工具使用规则]" in request["messages"][0]["content"]
    assert [t["function"]["name"] for t in request["tools"]] == list(ev.NATIVE_TOOLS)
    assert request["think"] is False and request["stream"] is False


def test_a_failing_request_is_a_failed_run_and_the_round_limit_is_enforced():
    def broken(payload):
        raise OSError("down")
    outcome = ev.run_scenario(BY_ID["neg_chat"], "m", broken, options={})
    assert ev.score(BY_ID["neg_chat"], outcome).startswith("请求失败")
    endless = Scripted(*[use(call("find_files", query="x"))] * ev.MAX_ROUNDS)
    assert "轮次上限" in ev.score(BY_ID["neg_chat"], ev.run_scenario(BY_ID["neg_chat"], "m", endless, options={}))


def test_evaluate_reports_pass_counts_and_a_readable_table():
    chat = Scripted(*[say("你好")] * 3)
    report = ev.evaluate("m", chat, repetitions=3, scenarios=[BY_ID["neg_chat"]], options={})
    assert (report["passed"], report["runs"]) == (3, 3)
    assert "3/3" in ev.summary_table(report)
