"""Credential IPC uses synthetic child processes only; never reads Keychain."""
import io
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from ai_desktop.services import credential_reader as reader
from ai_desktop.services.search_credentials import CredentialError, SearchCredentials
from tests.test_search_execution import context
from tests.test_web_search import FakeSecurity


def fake_child(monkeypatch, program):
    monkeypatch.setattr(reader, 'reader_command', lambda: [sys.executable, '-c', program])


def test_reader_returns_key_via_private_pipe_and_never_logs_it(monkeypatch, capsys):
    fake_child(monkeypatch, 'import sys; sys.stdin.read(); print(\'{"key":"test-secret"}\')')
    assert reader.read_background('exa') == 'test-secret'
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('reply', ['[]', '{}', '{"key":true}', '{"key":1}', '{"key":"bad secret"}',
                                  'secret-native-error', 'x'*8193])
def test_bad_child_response_has_safe_error(monkeypatch, reply):
    fake_child(monkeypatch, 'import sys; sys.stdin.read(); print('+repr(reply)+')')
    with pytest.raises(reader.CredentialReadError) as failure:
        reader.read_background('exa')
    assert 'secret' not in str(failure.value) and reply not in str(failure.value)


@pytest.mark.parametrize('operation', ['timeout', 'cancel', 'deadline'])
def test_blocked_native_read_is_killed_reaped_and_bounded(monkeypatch, operation):
    fake_child(monkeypatch, 'import time; time.sleep(60)')
    processes = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        assert kwargs['stderr'] == subprocess.DEVNULL
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(reader.subprocess, 'Popen', capture)
    cancelled = threading.Event()
    timer = threading.Timer(.08, cancelled.set) if operation == 'cancel' else None
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(reader.CredentialReadError):
            reader.read_background('parallel', cancelled=cancelled,
                                   deadline=started+.08 if operation == 'deadline' else None,
                                   timeout=.08 if operation == 'timeout' else 5)
    finally:
        if timer:
            timer.join()
    assert time.monotonic()-started < 1 and len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.parametrize('operation', ['cancel', 'deadline', 'provider'])
def test_invalid_or_interrupted_lookup_does_not_spawn(monkeypatch, operation):
    monkeypatch.setattr(reader.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start child'))
    cancelled = threading.Event()
    if operation == 'cancel':
        cancelled.set()
    with pytest.raises(reader.CredentialReadError):
        reader.read_background('invalid' if operation == 'provider' else 'exa', cancelled=cancelled,
                               deadline=time.monotonic()-1 if operation == 'deadline' else None)


def test_real_background_api_delegates_without_touching_native_backend(monkeypatch):
    ctx = context()
    seen = []
    monkeypatch.setattr(reader, 'read_background', lambda provider, **kwargs: seen.append((provider, kwargs)) or 'test')
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: pytest.fail('Must not read in worker'))
    assert SearchCredentials().get('exa', interactive=False, cancelled=ctx.cancelled,
                                   deadline=ctx.active_deadline) == 'test'
    assert seen == [('exa', {'cancelled': ctx.cancelled, 'deadline': ctx.active_deadline})]


def test_background_failure_maps_to_safe_credential_error(monkeypatch):
    def fail(*args, **kwargs):
        raise reader.CredentialReadError('读取已停止。')
    monkeypatch.setattr(reader, 'read_background', fail)
    with pytest.raises(CredentialError, match='读取已停止'):
        SearchCredentials().get('exa', interactive=False)


@pytest.mark.parametrize('frozen', [True, False])
def test_command_uses_same_frozen_identity_or_source_module(monkeypatch, frozen):
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    command = reader.reader_command()
    assert command[0] == sys.executable
    assert command[1:] == (['--search-credential-read'] if frozen else
                           ['-m', 'ai_desktop.services.credential_reader'])


def pipe(value=b'', tty=False):
    return SimpleNamespace(buffer=io.BytesIO(value), isatty=lambda: tty)


@pytest.mark.parametrize('invalid', ['stdin_tty', 'stdout_tty', 'parent', 'provider', 'native'])
def test_child_rejects_invalid_channel_without_secret_output(monkeypatch, invalid):
    payload = {'provider': 'wrong' if invalid == 'provider' else 'exa',
               'parent': -1 if invalid == 'parent' else os.getppid()}
    output = pipe(tty=invalid == 'stdout_tty')
    monkeypatch.setattr(sys, 'stdin', pipe(json.dumps(payload).encode(), tty=invalid == 'stdin_tty'))
    monkeypatch.setattr(sys, 'stdout', output)
    backend = FakeSecurity()
    backend.values['exa'] = b'test-secret'
    backend.SecKeychainSetUserInteractionAllowed = lambda allowed: -1
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: backend)
    assert reader.child_main() != 0 and output.buffer.getvalue() == b''


def test_child_disables_legacy_interaction_only_in_its_own_process(monkeypatch):
    backend = FakeSecurity()
    backend.values['exa'] = b'test-secret'
    states = []
    backend.SecKeychainSetUserInteractionAllowed = lambda allowed: states.append(allowed) or 0
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: backend)
    monkeypatch.setattr(sys, 'stdin', pipe(json.dumps({'provider': 'exa', 'parent': os.getppid()}).encode()))
    output = pipe()
    monkeypatch.setattr(sys, 'stdout', output)
    assert reader.child_main() == 0 and states == [False]
    assert json.loads(output.buffer.getvalue()) == {'key': 'test-secret'}


def test_preloaded_native_bridge_still_uses_bounded_read(monkeypatch):
    store = SearchCredentials()
    store._backend = FakeSecurity()  # Loading the bridge must not bypass isolation.
    monkeypatch.setattr(reader, 'read_background', lambda *a, **k: 'pipe-key')
    assert store.get('exa', interactive=False) == 'pipe-key'
