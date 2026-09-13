"""Async service/model/update checks and controller cache integration."""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import QPoint, QTimer

from ai_desktop import config
from ai_desktop.llm.service_checks import (
    AsyncServiceChecks,
    ServiceCheckResult,
    ServiceState,
    model_cache_key,
)
from ai_desktop.main import ChatController
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.utils.storage import get_setting, save_setting
from tests.fake_ollama import FakeOllama


def json_body(value) -> list[bytes]:
    return [json.dumps(value, ensure_ascii=False).encode("utf-8")]


@pytest.fixture
def checker(qtbot):
    value = AsyncServiceChecks(service_timeout_ms=5000, update_timeout_ms=5000)
    yield value
    value.cancel_all()
    value.deleteLater()


@pytest.mark.parametrize(("body", "state", "models"), [
    ({"models": [{"name": "qwen:7b"}, {"name": "llama:8b"}]},
     ServiceState.ONLINE, ("qwen:7b", "llama:8b")),
    ({"models": []}, ServiceState.EMPTY, ()),
    ({"unexpected": []}, ServiceState.INVALID, ()),
    ({"models": [{"size": 1}]}, ServiceState.INVALID, ()),
])
def test_service_response_states(qtbot, ollama_server, checker, body, state, models):
    ollama_server.enqueue(chunks=json_body(body))
    with qtbot.waitSignal(checker.service_checked, timeout=1000) as signal:
        checker.check_service(ollama_server.url + "/")
    result = signal.args[0]
    assert result.state == state
    assert result.models == models
    assert result.base_url == ollama_server.url


def test_http_failure_is_offline_not_empty(qtbot, ollama_server, checker):
    ollama_server.enqueue(chunks=json_body({"models": []}), status=503)
    with qtbot.waitSignal(checker.service_checked, timeout=1000) as signal:
        checker.check_service(ollama_server.url)
    result = signal.args[0]
    assert result.state == ServiceState.OFFLINE
    assert "503" in result.error


def test_malformed_json_is_invalid(qtbot, ollama_server, checker):
    ollama_server.enqueue(chunks=[b"not-json"])
    with qtbot.waitSignal(checker.service_checked, timeout=1000) as signal:
        checker.check_service(ollama_server.url)
    assert signal.args[0].state == ServiceState.INVALID


def test_service_timeout_has_one_offline_result(qtbot, ollama_server):
    checker = AsyncServiceChecks(service_timeout_ms=40)
    scenario = ollama_server.enqueue(before_headers=True)
    delivered = []
    checker.service_checked.connect(delivered.append)
    checker.check_service(ollama_server.url)
    qtbot.waitUntil(lambda: bool(delivered), timeout=1000)
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    qtbot.wait(20)
    assert len(delivered) == 1
    assert delivered[0].state == ServiceState.OFFLINE
    assert delivered[0].error == "连接超时"
    checker.deleteLater()


def test_same_address_inflight_check_is_deduplicated(qtbot, ollama_server, checker):
    scenario = ollama_server.enqueue(before_headers=True)
    first = checker.check_service(ollama_server.url)
    second = checker.check_service(ollama_server.url + "/")
    qtbot.waitUntil(scenario.received.is_set)
    assert first == second
    assert checker.service_request_count == 1
    emitted = []
    checker.service_checked.connect(emitted.append)
    checker.cancel_service()
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    qtbot.wait(20)
    assert emitted == []


def test_five_second_wait_budget_keeps_qt_event_loop_responsive(qtbot, ollama_server, checker):
    """A server that can outwait the 5s budget must not delay a 30ms GUI callback."""
    scenario = ollama_server.enqueue(before_headers=True)
    pulse = []
    started = time.perf_counter()
    checker.check_service(ollama_server.url)
    QTimer.singleShot(30, lambda: pulse.append(time.perf_counter()))
    qtbot.waitUntil(scenario.received.is_set)
    qtbot.waitUntil(lambda: bool(pulse), timeout=500)
    assert pulse[0] - started < 0.5
    assert checker._service_active is not None
    checker.cancel_service()
    qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)


def test_switching_address_aborts_a_and_only_delivers_b(qtbot, checker):
    server_a = FakeOllama()
    server_b = FakeOllama()
    try:
        waiting_a = server_a.enqueue(before_headers=True)
        server_b.enqueue(chunks=json_body({"models": [{"name": "model-b"}]}))
        sequence_a = checker.check_service(server_a.url)
        qtbot.waitUntil(waiting_a.received.is_set)
        delivered = []
        checker.service_checked.connect(delivered.append)
        sequence_b = checker.check_service(server_b.url)
        qtbot.waitUntil(lambda: bool(delivered), timeout=1000)
        qtbot.waitUntil(waiting_a.disconnected.is_set, timeout=1000)
        assert sequence_b > sequence_a
        assert [(item.sequence, item.base_url, item.models) for item in delivered] == [
            (sequence_b, server_b.url, ("model-b",)),
        ]
    finally:
        checker.cancel_all()
        server_a.close()
        server_b.close()


def test_update_check_uses_async_transport(qtbot, ollama_server, checker):
    ollama_server.enqueue(chunks=json_body({
        "tag_name": "v99.0.0",
        "html_url": "https://example.test/releases/v99.0.0",
        "body": "changes",
    }))
    with qtbot.waitSignal(checker.update_checked, timeout=1000) as signal:
        assert checker.check_for_update(force=True, url=f"{ollama_server.url}/release")
    assert signal.args[0].version == "99.0.0"
    assert ollama_server.requests == [{"method": "GET", "path": "/release"}]


@pytest.fixture
def controller(qtbot, tmp_db):
    with patch("ai_desktop.main.FloatButton"), patch("ai_desktop.main.MenuBarIcon"):
        with patch.object(ChatController, "_create_hotkey_backend", return_value=MagicMock()):
            value = ChatController()
    value._dialog = ChatDialog(value._all_agents, value._active_agent, [value._model], value._model)
    value._dialog.model_changed.connect(value._on_model_changed)
    qtbot.addWidget(value._dialog)
    yield value
    value.stop()
    qtbot.waitUntil(lambda: value._stopped, timeout=1000)


def test_cache_is_scoped_by_normalized_service_address(controller):
    save_setting(model_cache_key("http://service-a:11434/"), '["model-a"]')
    save_setting(model_cache_key("http://service-b:11434"), '["model-b"]')
    assert controller._load_cached_models("http://service-a:11434") == ["model-a"]
    assert controller._load_cached_models("http://service-b:11434/") == ["model-b"]


def test_open_window_shows_address_cache_without_waiting_for_network(
    qtbot, controller, ollama_server,
):
    scenario = ollama_server.enqueue(before_headers=True)
    controller._dialog.close()
    controller._dialog = None
    controller._model = "cached-model"
    save_setting(model_cache_key(ollama_server.url), '["cached-model"]')
    controller.float_btn.mapToGlobal.return_value = QPoint(900, 500)
    started = time.perf_counter()
    with patch("ai_desktop.main.pin_to_all_spaces"), \
            patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        controller._show_dialog()
    assert time.perf_counter() - started < 0.5
    assert controller._dialog._model_combo.currentText() == "cached-model"
    assert "检测" in controller._dialog._ollama_dot.toolTip()
    qtbot.waitUntil(scenario.received.is_set, timeout=1000)


def test_offline_result_keeps_cache_but_never_sets_online(controller, monkeypatch):
    base_url = "http://offline.test:11434"
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", base_url)
    controller._dialog.set_cached_models(["cached-model"], "cached-model")
    controller._service_check_sequence = 4
    controller._service_check_url = base_url
    controller._on_service_checked(ServiceCheckResult(4, base_url, ServiceState.OFFLINE, error="refused"))
    assert controller._dialog._model_combo.currentText() == "cached-model"
    assert "未连接" in controller._dialog._ollama_dot.toolTip()
    assert "缓存" in controller._dialog._ollama_dot.toolTip()


def test_online_result_replaces_missing_model_and_records_fallback(controller, monkeypatch):
    base_url = "http://online.test:11434"
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", base_url)
    controller._model = "removed-model"
    controller._dialog.set_cached_models(["removed-model"], "removed-model")
    controller._service_check_sequence = 8
    controller._service_check_url = base_url
    controller._on_service_checked(
        ServiceCheckResult(8, base_url, ServiceState.ONLINE, ("available-model",)),
    )
    assert controller._model == "available-model"
    assert "已切换" in controller._dialog._model_combo.toolTip()
    assert json.loads(get_setting(model_cache_key(base_url))) == ["available-model"]
