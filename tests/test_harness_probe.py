"""Verify H0 evaluation boundaries; these are not real-model admission tests."""

import copy
import io
import json

import pytest

from ai_desktop.llm.harness_probe import (
    TASKS,
    LocalOllama,
    ProbeError,
    ProbeRecorder,
    assemble_complete_frames,
    distribution,
    evaluate_model,
    fixture_result,
    recheck_tools,
    run_task,
    score_task,
    tool_message,
    validate_message,
)


def call(name="bash", arguments=None, identifier=None):
    result = {"function": {"name": name, "arguments": arguments or {"command": "cat note.txt"}}}
    if identifier is not None:
        result["id"] = identifier
    return result


def response(content="", calls=None, reason="stop"):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"message": message, "done": True, "done_reason": reason, "eval_count": 10, "prompt_eval_count": 50}


class Transport:
    base_url = "http://127.0.0.1:11434"

    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.requests = []

    def request(self, path, payload=None):
        self.requests.append((path, copy.deepcopy(payload)))
        if path == "/api/chat":
            item = next(self.responses)
            if isinstance(item, Exception):
                raise item
            return copy.deepcopy(item)
        if path == "/api/version":
            return {"version": "test-version"}
        if path == "/api/tags":
            return {
                "models": [
                    {"name": "local", "digest": "sha-local"},
                    {"name": "cloud", "digest": "sha-cloud", "remote_host": "https://example.com"},
                ]
            }
        if path == "/api/show":
            return {
                "capabilities": ["completion", "thinking", "tools"],
                "thinking": {"values": [False, True], "default": True},
            }
        raise AssertionError(path)


def recorder(tmp_path, responses=()):
    r = ProbeRecorder(Transport(responses), tmp_path / "report")
    r.discover(("local",))
    return r


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://example.com",
        "http://localhost@evil.com",
        "http://localhost/path",
        "http://localhost?next=evil",
        "http://user:pass@localhost",
    ],
)
def test_loopback_only(url):
    with pytest.raises(ValueError):
        LocalOllama(url)


def test_cloud_and_missing_models_never_show_or_generate(tmp_path):
    r = ProbeRecorder(Transport(), tmp_path / "report")
    d = r.discover(("cloud", "missing"))
    assert d["profiles"]["cloud"]["status"] == "cloud_refused"
    assert d["profiles"]["missing"]["status"] == "not_installed"
    assert [path for path, _ in r.transport.requests] == ["/api/version", "/api/tags"]


def test_raw_response_and_failures_saved_before_propagation(tmp_path):
    raw = response("测试", [call(identifier="id-1")])
    r = recorder(tmp_path, [raw, ProbeError("transport")])
    r.chat("local", [], False, task_id="sample", num_predict=100, tools=[])
    with pytest.raises(ProbeError):
        r.chat("local", [], False, task_id="failure", num_predict=100, tools=[])
    rows = [json.loads(line) for line in r.path.read_text().splitlines()]
    assert rows[0]["response"] == raw
    assert rows[0]["thinking_tokens"] is None
    assert rows[0]["digest"] == "sha-local"
    assert rows[1]["error_code"] == "transport"


def test_never_overwrite_observations(tmp_path):
    r = recorder(tmp_path, [response("ok")])
    r.chat("local", [], False, task_id="one", num_predict=100, tools=[])
    with pytest.raises(ValueError):
        ProbeRecorder(Transport(), r.directory)


def test_length_with_partial_calls_does_not_dispatch(tmp_path):
    partial = {"function": {"name": "bash", "arguments": '{"command":"cat'}}
    r = recorder(tmp_path, [response("partial", [partial], "length")])
    result = run_task(r, "local", TASKS[1], False, 1)
    assert result == {"status": "limited", "reason": "length", "trace": [], "partial": "partial", "passed": False}
    assert len(r.rows) == 1


def test_success_requires_tool_evidence_not_plausible_answer(tmp_path):
    r = recorder(tmp_path, [response("AMBER-731")])
    result = run_task(r, "local", TASKS[1], False, 1024)
    assert not result["passed"]


def test_duplicate_calls_keep_separate_results_and_provider_ids(tmp_path):
    calls = [call(identifier="first"), call(identifier="second")]
    r = recorder(tmp_path, [response(calls=calls), response("AMBER-731")])
    result = run_task(r, "local", TASKS[3], False, 1024)
    assert result["passed"]
    assert len({row["local_call_id"] for row in result["trace"]}) == 2
    messages = r.transport.requests[-1][1]["messages"]
    assert [msg["tool_call_id"] for msg in messages if msg["role"] == "tool"] == ["first", "second"]


def test_no_id_wire_does_not_invent_provider_identifier():
    assert "tool_call_id" not in tool_message(call(), {"stdout": "ok"})


def test_duplicate_provider_id_is_protocol_error():
    with pytest.raises(ProbeError):
        validate_message(response(calls=[call(identifier="same"), call(identifier="same")]))


@pytest.mark.parametrize(
    "bad", [[], {"done": False}, response(calls=[{"function": {"name": "bash", "arguments": "{}"}}])]
)
def test_incomplete_or_ambiguous_message_rejected(bad):
    with pytest.raises(ProbeError):
        validate_message(bad)


def test_search_and_total_call_budgets_stop_before_dispatch(tmp_path):
    calls = [call("web_search", {"query": "quasar"}) for _ in range(7)]
    r = recorder(tmp_path, [response(calls=calls)])
    assert run_task(r, "local", TASKS[2], False, 1024)["trace"] == []
    r2 = recorder(tmp_path / "second", [response(calls=[call() for _ in range(17)])])
    assert run_task(r2, "local", TASKS[1], False, 1024)["reason"] == "calls"


def test_failure_recovery_requires_observed_failure():
    trace = [{"name": "bash", "result": {"stdout": "AMBER-731"}}]
    assert not score_task(TASKS[8], {"content": "AMBER-731"}, trace)
    trace.insert(0, {"name": "bash", "result": {"error_type": "not_found"}})
    assert score_task(TASKS[8], {"content": "AMBER-731"}, trace)
    assert not score_task(TASKS[8], {"content": "AMBER-731"}, list(reversed(trace)))


def test_plausible_final_code_not_in_tool_results_is_failure():
    trace = [{"name": "web_search", "result": {"sources": [{"excerpt": "unrelated"}]}}]
    assert not score_task(TASKS[2], {"content": "ORBIT-924"}, trace)


@pytest.mark.parametrize(
    "command", ["ls && rm -rf /", "$(pwd)", "cat ../../secret", "python -c 'print(1)'", "cat 'unfinished"]
)
def test_fixture_never_executes_host_commands(command):
    assert fixture_result(call(arguments={"command": command})).get("error_type")


def test_short_measurement_cannot_advance_to_budget(tmp_path):
    r = recorder(tmp_path, [response("13 17")])
    profile = {"digest": "sha-local", "capabilities": ["completion", "tools"], "thinking": None}
    result = evaluate_model(r, "local", profile, repetitions=2)
    assert not result["admitted"]
    assert result["stages"]["H0.3"]["status"] == "pending"
    assert not r.rows


def test_named_levels_not_guessed_as_booleans(tmp_path):
    r = recorder(tmp_path)
    profile = {"digest": "sha-local", "capabilities": ["thinking", "tools"], "thinking": {"values": ["low", "xhigh"]}}
    result = evaluate_model(r, "local", profile)
    assert not result["admitted"]
    assert result["stages"]["H0.2"]["status"] == "pending"
    assert not r.rows


def test_distribution_uses_small_sample_nearest_rank():
    assert distribution([1, 2, 3, 4, 5]) == {"samples": 5, "p50": 3, "p90": 5, "p95": 5, "max": 5}


def test_stream_preserves_text_and_separate_identical_calls():
    frames = [response("你好", [call(identifier="first")]), response(" world", [call(identifier="second")])]
    frames[0]["done"] = False
    result = assemble_complete_frames(frames)
    assert result["message"]["content"] == "你好 world"
    assert len(result["message"]["tool_calls"]) == 2
    assert result["eval_count"] == 10


def test_stream_does_not_guess_partial_argument_merge():
    frame = response(calls=[{"function": {"name": "bash", "arguments": '{"command":'}}])
    with pytest.raises(ProbeError):
        assemble_complete_frames([frame])


@pytest.mark.parametrize("frames", [[], [{"done": False}], [response(), response()]])
def test_missing_or_duplicate_stream_completion_rejected(frames):
    with pytest.raises(ProbeError):
        assemble_complete_frames(frames)


def test_length_stream_does_not_validate_truncated_tools():
    frame = response("partial", calls=[{"function": {"arguments": "unfinished"}}], reason="length")
    assert assemble_complete_frames([frame])["done_reason"] == "length"


def test_unknown_completion_never_dispatches_tools(tmp_path):
    r = recorder(tmp_path, [response(calls=[call()], reason="unknown")])
    with pytest.raises(ProbeError, match="unknown_done_reason"):
        run_task(r, "local", TASKS[1], False, 1024)
    assert len(r.rows) == 1


def test_missing_token_metrics_cannot_advance_to_budget_selection(tmp_path):
    broken = response("answer")
    broken["eval_count"] = None
    r = recorder(tmp_path, [broken] * 15)
    profile = {"digest": "sha-local", "capabilities": ["completion", "tools"], "thinking": None}
    result = evaluate_model(r, "local", profile)
    assert result["stages"]["H0.3"]["status"] == "pending"
    assert "H0.4" not in result["stages"]
    assert not result["admitted"]


def test_round_limit_preserves_completed_calls_without_replaying(tmp_path):
    r = recorder(tmp_path, [response(calls=[call()]), response(calls=[call()])])
    result = run_task(r, "local", TASKS[1], False, 1024, max_rounds=2)
    assert result["reason"] == "rounds"
    assert len(result["trace"]) == 2
    assert len(r.rows) == 2


def test_transport_captures_utf8_ndjson_without_final_newline():
    frames = [response("你好"), response("世界")]
    frames[0]["done"] = False
    body = "\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames).encode("utf-8")
    client = LocalOllama()

    class Opener:
        def open(self, request, timeout):
            assert json.loads(request.data)["stream"] is True
            return io.BytesIO(body)

    client.opener = Opener()
    captured = client.stream({"stream": True})
    assert captured == frames
    assert assemble_complete_frames(captured)["message"]["content"] == "你好世界"


@pytest.mark.parametrize("body", [b"not JSON\n", b'{"done":false}\n', b"x" * (1024 * 1024 + 1)])
def test_stream_transport_rejects_eof_malformed_and_oversized_frames(body):
    client = LocalOllama()

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(body)

    client.opener = Opener()
    with pytest.raises(ProbeError):
        client.stream({"stream": True})


def test_recheck_rejects_changed_model_version_before_any_request(tmp_path):
    r = recorder(tmp_path)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"discovery": {"ollama_version": "old"}, "results": {}}))
    with pytest.raises(ValueError, match="mismatch"):
        recheck_tools(r, "local", {"digest": "sha-local"}, baseline)
    assert not r.rows


def test_recheck_rejects_prompt_changes_before_any_request(tmp_path):
    r = recorder(tmp_path)
    baseline = tmp_path / "baseline.json"
    stages = {
        "H0.2": {"status": "verified"},
        "H0.3": {"status": "measured"},
        "H0.4": {
            "status": "verified",
            "task_profile": {"think": False, "num_ctx": 8192, "num_predict": 1024},
            "protocol_and_context": {"stream_verified": True, "pressure_verified": True},
        },
    }
    baseline.write_text(
        json.dumps(
            {
                "discovery": {"ollama_version": "test-version"},
                "results": {"local": {"digest": "sha-local", "stages": stages}},
            }
        )
    )
    (tmp_path / "observations.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "task_id": "measure-simple",
                    "digest": "sha-local",
                    "ollama_version": "test-version",
                    "request": {"think": False, "messages": [{"role": "system", "content": "different prompt"}]},
                }
            )
            for _ in range(15)
        )
    )
    with pytest.raises(ValueError, match="prompt mismatch"):
        recheck_tools(r, "local", {"digest": "sha-local"}, baseline)
    assert not r.rows
