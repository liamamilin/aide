"""The headless core speaks NDJSON on stdio and never loads Qt."""
import json
import os
import subprocess
import sys

import pytest

from ai_desktop.core import protocol


def _spawn(tmp_path):
    env = {**os.environ, "AIDE_DATA_DIR": str(tmp_path)}
    return subprocess.Popen(
        [sys.executable, "-m", "ai_desktop.core"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )


def _call(proc, req_id, method, params=None):
    proc.stdin.write(json.dumps({"id": req_id, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


@pytest.fixture
def core(tmp_path):
    proc = _spawn(tmp_path)
    ready = json.loads(proc.stdout.readline())
    yield proc, ready
    proc.stdin.close()
    proc.wait(timeout=10)


def test_ready_event_then_ping_and_health(core, tmp_path):
    proc, ready = core
    assert ready["event"] == "core.ready"
    assert ready["data"]["protocol"] == protocol.PROTOCOL_VERSION
    assert _call(proc, "1", "ping") == {"id": "1", "result": {"pong": True}}
    health = _call(proc, "2", "health")["result"]
    assert health["qt_loaded"] is False
    assert health["data_dir"] == str(tmp_path)


def test_errors_keep_the_core_alive(core):
    proc, _ = core
    assert _call(proc, "3", "nope")["error"]["code"] == protocol.ERR_UNKNOWN_METHOD
    proc.stdin.write("not json\n")
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["error"]["code"] == protocol.ERR_PARSE
    proc.stdin.write('{"method":"ping"}\n')
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["error"]["code"] == protocol.ERR_INVALID
    assert _call(proc, "4", "ping")["result"]["pong"] is True


def test_exits_on_stdin_eof(tmp_path):
    proc = _spawn(tmp_path)
    proc.stdout.readline()
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0


def test_stray_print_cannot_corrupt_stdout():
    from io import StringIO

    from ai_desktop.core.server import Server

    out = StringIO()
    server = Server(StringIO('{"id":"1","method":"boom"}\n'), out, {"boom": lambda p: 1 / 0})
    server.serve()
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["error"]["code"] == protocol.ERR_INTERNAL
