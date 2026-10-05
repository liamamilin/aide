"""Acceptance cannot launch against user data or restart the wrong executable."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_desktop.diagnostics import harness_acceptance as diagnostic
from scripts import harness_acceptance as launcher


@pytest.fixture
def isolated_env():
    with tempfile.TemporaryDirectory(prefix='aide-acceptance-') as directory:
        root = Path(directory).resolve()
        yield {'AIDE_ACCEPTANCE_ROOT': str(root), 'AIDE_DATA_DIR': str(root/'data'),
               'AIDE_LOG_DIR': str(root/'logs')}


def test_diagnostic_accepts_only_matching_isolated_paths(isolated_env):
    assert diagnostic.isolated_root(isolated_env) == Path(isolated_env['AIDE_ACCEPTANCE_ROOT'])


@pytest.mark.parametrize('variable', ['AIDE_ACCEPTANCE_ROOT', 'AIDE_DATA_DIR', 'AIDE_LOG_DIR'])
def test_missing_isolation_variable_is_rejected(isolated_env, variable):
    isolated_env.pop(variable)
    with pytest.raises(ValueError):
        diagnostic.isolated_root(isolated_env)


@pytest.mark.parametrize('variable', ['AIDE_DATA_DIR', 'AIDE_LOG_DIR'])
def test_user_data_or_logs_are_rejected(isolated_env, variable, tmp_path):
    isolated_env[variable] = str(tmp_path/'user-data')
    with pytest.raises(ValueError, match='data and logs must be isolated'):
        diagnostic.isolated_root(isolated_env)


def test_isolation_rejects_symlink_to_external_data(isolated_env, tmp_path):
    Path(isolated_env['AIDE_DATA_DIR']).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='data and logs must be isolated'):
        diagnostic.isolated_root(isolated_env)


def test_restart_rejects_other_directory_before_opening_database(isolated_env, monkeypatch, tmp_path):
    for key, value in isolated_env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match='same isolated directory'):
        diagnostic.resume_probe(tmp_path, 1, 'orphan')


@pytest.mark.parametrize('frozen', [False, True])
def test_restart_command_uses_actual_runtime(monkeypatch, frozen):
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'executable', '/test/runtime')
    expected = ['--harness-acceptance'] if frozen else ['-m', diagnostic.DIAGNOSTIC_MODULE]
    assert diagnostic.child_command() == ['/test/runtime', *expected]


@pytest.mark.parametrize('packaged', [False, True])
def test_launcher_replaces_user_paths_and_cleans_its_directory(monkeypatch, tmp_path, packaged):
    monkeypatch.setenv('AIDE_DATA_DIR', str(tmp_path/'existing-data'))
    monkeypatch.setenv('AIDE_LOG_DIR', str(tmp_path/'existing-logs'))
    seen = []
    executable = tmp_path/'App.app'/'Contents'/'MacOS'/'AI桌面助手'
    executable.parent.mkdir(parents=True)
    executable.write_text('fixture')
    executable.chmod(0o700)
    def run(command, *, cwd, env, check):
        root = diagnostic.isolated_root(env)
        seen.append(root)
        assert cwd == launcher.REPO and not check
        assert command[:2] == ([str(executable), '--harness-acceptance'] if packaged else
                                [sys.executable, '-m'])
        assert command[-2:] == ['--only', 'restart_and_readonly_history']
        assert root.is_dir() and env['AIDE_DATA_DIR'] != os.environ['AIDE_DATA_DIR']
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    args = ['--only', 'restart_and_readonly_history']
    if packaged:
        args += ['--packaged', str(tmp_path/'App.app')]
    assert launcher.main(args) == 7
    assert seen and not seen[0].exists()
    assert os.environ['AIDE_DATA_DIR'] == str(tmp_path/'existing-data')


def test_launcher_rejects_missing_bundle_before_starting_process(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a, **k: pytest.fail('Process must not start'))
    with pytest.raises(SystemExit) as failure:
        launcher.main(['--packaged', str(tmp_path/'Missing.app')])
    assert failure.value.code == 2


def test_unknown_case_cannot_produce_empty_success_report(isolated_env, monkeypatch):
    for key, value in isolated_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(diagnostic, 'acceptance', lambda *a: pytest.fail('Acceptance must not start'))
    with pytest.raises(SystemExit) as failure:
        diagnostic.main(['--only', 'unknown'])
    assert failure.value.code == 2
