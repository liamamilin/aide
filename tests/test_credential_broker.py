"""Synthetic IPC only; no test reads a real Keychain item."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ai_desktop.services import credential_broker as broker
from ai_desktop.services import credential_reader
from ai_desktop.services.search_credentials import CredentialError, SearchCredentials, probe_search_runtime
from tests.test_web_search import FakeSecurity


def child(monkeypatch, code):
    monkeypatch.setattr(broker, 'broker_command', lambda: [sys.executable, '-c', code])


def test_private_request_and_reply_without_logging(monkeypatch, capsys):
    child(monkeypatch, '''import json,os,sys
p=json.load(sys.stdin)
assert p['parent']==os.getppid() and p['operation']=='set'
assert p['provider']=='exa' and p['key']=='test-only' and not p['interactive']
print(json.dumps({'key':''}))''')
    assert broker.request('set', 'exa', key='test-only') == ''
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('reply', ['[]', '{}', '{"key":true}', '{"key":42}', '{"key":"bad secret"}',
                                  'secret-native-error', 'x'*8193, '{"key":"'+'x'*4097+'"}'])
def test_invalid_response_is_redacted(monkeypatch, reply):
    child(monkeypatch, 'import sys; sys.stdin.read(); print('+repr(reply)+')')
    with pytest.raises(broker.BrokerError) as failure:
        broker.request('get', 'parallel')
    assert 'secret' not in str(failure.value) and reply not in str(failure.value)


@pytest.mark.parametrize('kwargs', [dict(operation='wrong', provider='exa'), dict(operation='get', provider='wrong'),
                                    dict(operation='set', provider='exa', key='bad secret'),
                                    dict(operation='set', provider='exa', key='x'*4097),
                                    dict(operation='get', provider='exa', fixture='wrong'),
                                    dict(operation='set', provider='exa', key='中'*3000)])
def test_invalid_request_never_spawns(monkeypatch, kwargs):
    monkeypatch.setattr(broker.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(broker.BrokerError):
        broker.request(**kwargs)


@pytest.mark.parametrize('cause', ['cancel', 'deadline', 'timeout'])
def test_wait_is_cancellable_and_process_reaped(monkeypatch, cause):
    child(monkeypatch, 'import time; time.sleep(60)')
    processes = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        assert kwargs['stdin'] == subprocess.PIPE and kwargs['stdout'] == subprocess.PIPE
        assert kwargs['stderr'] == subprocess.DEVNULL
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(broker.subprocess, 'Popen', capture)
    cancelled = threading.Event()
    timer = threading.Timer(.08, cancelled.set) if cause == 'cancel' else None
    if timer:
        timer.start()
    monkeypatch.setattr(broker, 'BACKGROUND_TIMEOUT', .08 if cause == 'timeout' else 10)
    started = time.monotonic()
    try:
        with pytest.raises(broker.BrokerError):
            broker.request('get', 'exa', cancelled=cancelled,
                           deadline=started+.08 if cause == 'deadline' else None)
    finally:
        if timer:
            timer.join()
    assert time.monotonic()-started < 1
    assert len(processes) == 1 and processes[0].poll() is not None


def test_cancel_before_spawn(monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(broker.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not spawn'))
    with pytest.raises(broker.BrokerError):
        broker.request('get', 'exa', cancelled=cancelled)


def test_frozen_crud_connect_and_probe_share_identity(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    calls = []
    def invoke(operation, provider, **kwargs):
        calls.append((operation, provider, kwargs))
        return 'synthetic-key' if operation == 'get' else ''
    monkeypatch.setattr(broker, 'request', invoke)
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: pytest.fail('no direct App access'))
    store = SearchCredentials()
    assert store.get('exa', interactive=False) == 'synthetic-key'
    store.set('parallel', 'synthetic-key')
    store.delete('exa')
    assert store.connect('parallel') is True
    assert probe_search_runtime()['stable_broker']
    assert [entry[0] for entry in calls] == ['get', 'set', 'delete', 'get', 'get', 'probe']
    assert calls[0][2]['interactive'] is False and calls[3][2]['interactive'] is True
    assert calls[4][2]['interactive'] is False


def test_frozen_failure_never_falls_back_to_app_identity(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: pytest.fail('no fallback'))
    def fail(*args, **kwargs):
        raise broker.BrokerError('请连接钥匙串。')
    monkeypatch.setattr(broker, 'request', fail)
    with pytest.raises(CredentialError, match='请连接钥匙串'):
        SearchCredentials().get('exa', interactive=False)
    assert not probe_search_runtime()['keychain_available']


def test_allow_once_does_not_report_persistent_connection(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    def invoke(operation, provider, **kwargs):
        if kwargs['interactive']:
            return 'synthetic-key'
        raise broker.BrokerError('请连接钥匙串。')
    monkeypatch.setattr(broker, 'request', invoke)
    with pytest.raises(CredentialError):
        SearchCredentials().connect('exa')


def test_injected_backend_does_not_use_real_broker_in_frozen_tests(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(broker, 'request', lambda *a, **k: pytest.fail('never real keychain'))
    store = SearchCredentials(FakeSecurity())
    store.set('exa', 'synthetic')
    assert store.get('exa') == 'synthetic'
    store.delete('exa')


def test_old_frozen_cli_cannot_export_keys(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(SearchCredentials, '_security', lambda *a: pytest.fail('must not read'))
    assert credential_reader.child_main() == 2


def test_helper_missing_or_corrupted_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'executable', str(tmp_path/'Contents/MacOS/App'))
    with pytest.raises(broker.BrokerError):
        broker.broker_command()
    helpers = tmp_path/'Contents/Helpers'
    helpers.mkdir(parents=True)
    (helpers/'AIDESearchCredentials').write_bytes(b'corrupted')
    resources = tmp_path/'Contents/Resources'
    resources.mkdir()
    (resources/'credentials.json').write_text(json.dumps({'sha256': 'wrong'}))
    with pytest.raises(broker.BrokerError):
        broker.broker_command()


def test_connection_is_free_and_does_not_emit_secret(qtbot, monkeypatch):
    from ai_desktop.services.qt_credentials import CredentialConnectionJob, _active
    from ai_desktop.ui.settings_dialog import SettingsDialog
    calls = []
    class Credentials:
        def connect(self, provider, **kwargs):
            calls.append(provider)
            return True
    monkeypatch.setattr('ai_desktop.ui.settings_dialog.SearchJob', lambda *a: pytest.fail('no paid HTTP'))
    dialog = SettingsDialog({}, credentials=Credentials())
    qtbot.addWidget(dialog)
    dialog._widgets['search_provider'].setCurrentIndex(1)
    dialog._connect_credentials()
    assert not dialog._save_button.isEnabled()
    qtbot.waitUntil(lambda: dialog._credential_job is None)
    assert calls == ['exa']
    assert 'Exa 钥匙串已连接' in dialog._search_status.text() and dialog._save_button.isEnabled()
    assert dialog._search_job is None
    qtbot.waitUntil(lambda: not any(isinstance(job, CredentialConnectionJob) for job in _active))


def test_close_settings_cancels_connection_without_destroying_running_thread(qtbot):
    from ai_desktop.services.qt_credentials import _active
    from ai_desktop.ui.settings_dialog import SettingsDialog
    stopped = threading.Event()
    class Credentials:
        def connect(self, provider, *, cancelled):
            cancelled.wait(3)
            stopped.set()
            raise CredentialError('已停止。')
    dialog = SettingsDialog({}, credentials=Credentials())
    qtbot.addWidget(dialog)
    dialog._connect_credentials()
    job = dialog._credential_job
    dialog.reject()
    assert job.cancelled.is_set()
    qtbot.waitUntil(stopped.is_set)
    qtbot.waitUntil(lambda: job not in _active)


def test_native_source_has_fixed_namespace_and_verifies_parent():
    # Guard the trust boundary rather than mirroring animation/UI implementation.
    source = (Path(__file__).parents[1]/'native/search_credentials.m').read_text()
    assert 'SecCodeCopyGuestWithAttributes' in source and 'SecCodeCheckValidity' in source
    assert 'certificate leaf' in source and 'com.milin.ai-desktop-assistant' in source
    assert 'SecACL' not in source and 'PartitionId' not in source
    assert 'request[@"service"]' not in source and 'request[@"account"]' not in source
    assert 'S_ISFIFO' in source and 'SecKeychainSetUserInteractionAllowed' in source
