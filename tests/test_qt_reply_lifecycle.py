"""A stale PyQt wrapper must not turn a live native request into a failure."""
from unittest.mock import patch

import pytest
from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent

from ai_desktop.llm.chat_client import ChatClient
from ai_desktop.llm.events import ErrorCode, EventKind, ResultStatus
from ai_desktop.llm.qt_stream import QtChatTransport
from ai_desktop.services.qt_reply import call_reply
from ai_desktop.services.qt_search import SearchJob
from ai_desktop.services.web_search import SearchSettings, build_request
from tests.test_search_execution import search_server as search_server


def invalidated_post(manager):
    post = manager.post

    def send(*args):
        reply = post(*args)
        # Simulate the monitor's stale queued notification, without deleting
        # the native reply. Its manager still owns the same HTTP request.
        sip.setdeleted(reply)
        return reply

    return send


@pytest.mark.parametrize('stage', ['post', 'headers', 'ready_read', 'chunk'])
def test_model_recovers_live_native_reply(qtbot, ollama_server, stage):
    ollama_server.enqueue({'message': {'content': 'partial'}},
                          {'message': {'content': ' answer'}, 'done': True}, delay=.06)
    transport = QtChatTransport(ChatClient().create_request([]))
    results, chunks = [], []
    transport.done.connect(results.append)
    transport.stream_event.connect(chunks.append)
    if stage == 'chunk':
        def invalidate(event):
            if event.kind == EventKind.CONTENT and event.text == 'partial':
                sip.setdeleted(transport._reply)
        transport.stream_event.connect(invalidate)
    try:
        if stage == 'post':
            with patch.object(transport._manager, 'post', side_effect=invalidated_post(transport._manager)):
                transport.start(b'{}')
        else:
            transport.start(b'{}')
            if stage != 'chunk':
                qtbot.waitUntil(lambda: any(event.kind == EventKind.CONTENT for event in chunks))
                sip.setdeleted(transport._reply)
                getattr(transport, '_' + ('headers_received' if stage == 'headers' else stage))()
        qtbot.waitUntil(lambda: bool(results))
        transport.cancel()
        assert len(results) == 1 and results[0].status == ResultStatus.SUCCEEDED
        assert results[0].text == 'partial answer'
        assert len(ollama_server.requests) == 1
        assert transport._reply is None
        assert not transport._connect_timer.isActive() and not transport._idle_timer.isActive()
    finally:
        transport.cancel()
        transport.deleteLater()


@pytest.mark.parametrize('action,status', [
    ('cancel', ResultStatus.CANCELLED), ('limit', ResultStatus.LIMITED),
    ('_idle_timeout', ResultStatus.FAILED), ('_connection_timeout', ResultStatus.FAILED),
])
def test_model_releases_native_reply_after_wrapper_invalidation(qtbot, ollama_server, action, status):
    scenario = ollama_server.enqueue({'message': {'content': 'partial'}}, hold_open=True)
    transport = QtChatTransport(ChatClient().create_request([]))
    results, chunks = [], []
    transport.done.connect(results.append)
    transport.stream_event.connect(chunks.append)
    try:
        transport.start(b'{}')
        qtbot.waitUntil(lambda: any(event.kind == EventKind.CONTENT for event in chunks))
        sip.setdeleted(transport._reply)
        getattr(transport, action)()
        transport._finished()
        transport.cancel()
        assert len(results) == 1 and results[0].status == status
        assert results[0].text == 'partial'
        if status == ResultStatus.FAILED:
            assert results[0].error_code == ErrorCode.TIMEOUT
        assert transport._reply is None
        assert not transport._connect_timer.isActive() and not transport._idle_timer.isActive()
        qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
        assert len(ollama_server.requests) == 1
    finally:
        transport.cancel()
        transport.deleteLater()


@pytest.mark.parametrize('stage', ['post', 'read'])
def test_search_recovers_live_native_reply(qtbot, search_server, stage):
    search_server.enqueue(chunks=[b'{"results":[', b'{"title":"Python","url":"https://docs.python.org/"}]}'],
                          delay=.06)
    job = SearchJob(build_request(SearchSettings('exa', 1), 'test-only-key', 'Python'))
    results = []
    job.finished.connect(results.append)
    try:
        if stage == 'post':
            with patch.object(job._manager, 'post', side_effect=invalidated_post(job._manager)):
                job.start()
        else:
            job.start()
            qtbot.waitUntil(lambda: bool(job._data))
            sip.setdeleted(job._reply)
            job._read()
        qtbot.waitUntil(lambda: bool(results))
        job.cancel()
        assert len(results) == 1 and results[0].ok
        assert results[0].sources[0].title == 'Python'
        assert len(search_server.requests) == 1
        assert job._reply is None and not job._timer.isActive()
    finally:
        job.cancel()
        job.deleteLater()


@pytest.mark.parametrize('action,kind', [('cancel', 'cancelled'), ('limit', 'active_limit'), ('_timeout', 'timeout')])
def test_search_releases_native_reply_after_wrapper_invalidation(qtbot, search_server, action, kind):
    scenario = search_server.enqueue(before_headers=True)
    job = SearchJob(build_request(SearchSettings('exa', 1), 'test-only-key', 'Python'))
    results = []
    job.finished.connect(results.append)
    try:
        job.start()
        qtbot.waitUntil(scenario.received.is_set)
        sip.setdeleted(job._reply)
        getattr(job, action)()
        job._done()
        job.cancel()
        assert len(results) == 1 and results[0].error_type == kind
        assert job._reply is None and not job._timer.isActive()
        qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
        assert len(search_server.requests) == 1
    finally:
        job.cancel()
        job.deleteLater()


def test_search_native_destruction_finishes_once(qtbot, search_server):
    scenario = search_server.enqueue(before_headers=True)
    job = SearchJob(build_request(SearchSettings('exa', 1), 'test-only-key', 'Python'))
    results = []
    job.finished.connect(results.append)
    try:
        job.start()
        qtbot.waitUntil(scenario.received.is_set)
        sip.delete(job._reply)
        qtbot.waitUntil(lambda: bool(results), timeout=500)
        job._read()
        job._done()
        job.cancel()
        assert len(results) == 1 and results[0].error_type == 'network'
        assert job._reply is None and not job._timer.isActive()
        qtbot.waitUntil(scenario.disconnected.is_set, timeout=1000)
    finally:
        job.cancel()
        job.deleteLater()


@pytest.mark.parametrize('invalidate', [False, True])
def test_binding_call_race_recovers_only_invalid_wrappers(qtbot, ollama_server, invalidate):
    scenario = ollama_server.enqueue(before_headers=True)
    transport = QtChatTransport(ChatClient().create_request([]))
    calls = []
    try:
        transport.start(b'{}')
        qtbot.waitUntil(scenario.received.is_set)
        address = sip.unwrapinstance(transport._reply)

        def operation(reply):
            calls.append(reply)
            if len(calls) == 1:
                if invalidate:
                    sip.setdeleted(reply)
                    return reply.isRunning()  # Real binding exception, before any operation.
                raise RuntimeError('operation error on a live reply')
            return reply.isRunning()

        if invalidate:
            reply, running = call_reply(transport._manager, transport._reply, operation)
            assert running and sip.unwrapinstance(reply) == address and len(calls) == 2
        else:
            with pytest.raises(RuntimeError, match='operation error on a live reply'):
                call_reply(transport._manager, transport._reply, operation)
            assert len(calls) == 1 and not sip.isdeleted(calls[0])
        assert len(ollama_server.requests) == 1
    finally:
        transport.cancel()
        transport.deleteLater()


@pytest.mark.parametrize('kind', ['model', 'search'])
def test_recovered_reply_and_native_owner_tree_are_released(qtbot, ollama_server, search_server, kind):
    server = ollama_server if kind == 'model' else search_server
    scenario = server.enqueue(before_headers=True)
    owner = (QtChatTransport(ChatClient().create_request([])) if kind == 'model' else
             SearchJob(build_request(SearchSettings('exa', 1), 'test-only-key', 'Python')))
    destroyed = []
    try:
        owner.start(b'{}') if kind == 'model' else owner.start()
        qtbot.waitUntil(scenario.received.is_set)
        sip.setdeleted(owner._reply)
        owner._call_reply(lambda reply: reply.isRunning())
        objects = [('owner', owner), ('manager', owner._manager), ('reply', owner._reply)]
        timers = ([owner._connect_timer, owner._idle_timer] if kind == 'model' else [owner._timer])
        objects += [(f'timer-{index}', timer) for index, timer in enumerate(timers)]
        for name, obj in objects:
            obj.destroyed.connect(lambda _=None, name=name: destroyed.append(name))
        owner.cancel()
        qtbot.waitUntil(scenario.disconnected.is_set)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sorted(destroyed) == sorted(name for name, _ in objects)
        assert all(sip.isdeleted(obj) for _, obj in objects)
    finally:
        if not sip.isdeleted(owner):
            owner.cancel()
            owner.deleteLater()


@pytest.mark.parametrize('kind', ['model', 'search'])
def test_callbacks_before_start_do_not_close_an_unstarted_request(qtbot, ollama_server, search_server, kind):
    server = ollama_server if kind == 'model' else search_server
    server.enqueue({'message': {'content': 'answer'}, 'done': True} if kind == 'model' else {'results': []})
    owner = (QtChatTransport(ChatClient().create_request([])) if kind == 'model' else
             SearchJob(build_request(SearchSettings('exa', 1), 'test-only-key', 'Python')))
    try:
        if kind == 'model':
            owner._headers_received()
            owner._ready_read()
            owner._finished()
        else:
            owner._read()
            owner._done()
        assert owner.result is None and not server.requests
        owner.start(b'{}') if kind == 'model' else owner.start()
        # Repeated start never posts another HTTP request, even after recovery.
        owner.start(b'{}') if kind == 'model' else owner.start()
        qtbot.waitUntil(lambda: owner.result is not None)
        assert owner.result.ok and len(server.requests) == 1
    finally:
        owner.cancel()
        owner.deleteLater()
