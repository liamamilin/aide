"""Execution boundary tests in temporary workspaces; no real user commands."""
import json
import os
import shlex
import sys
import threading
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_desktop.llm.active_budget import ActiveBudget
from ai_desktop.llm.chat_client import ChatClient, _payload
from ai_desktop.llm.events import ChatResult, ResultStatus
from ai_desktop.llm.ollama_protocol import ModelTurn, ToolCall
from ai_desktop.llm.run_loop import RunLoop
from ai_desktop.llm.run_types import RunContext, RunLimits, ToolExecutionContext, ToolOutput, ToolSpec
from ai_desktop.services.bash_executor import BashExecutor, BashResult, run_command
from ai_desktop.services.bash_policy import CommandDisposition, classify_command
from ai_desktop.services.command_confirmation import CommandConfirmation, ConfirmationBroker, ConfirmationOutcome
from ai_desktop.services.execution_context import (
    BashPolicy,
    ExecutionSnapshot,
    load_execution_preferences,
    load_workspace_policy,
    save_execution_preferences,
    save_workspace_policy,
)
from ai_desktop.utils.storage import Message


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'notes.txt').write_text('hello\nworld\n')
    (root / 'file with space').write_text('spaces')
    return ExecutionSnapshot.create(root)


def context(snapshot, *, command='cat notes.txt', seconds=300):
    request = ChatClient().create_request([], agent_id='general_assistant', think=False)
    call = ToolCall('call', 'bash', json.dumps({'command':command}))
    budget = ActiveBudget(seconds)
    return ToolExecutionContext(request, call, threading.Event(), budget.deadline, snapshot, budget)


@pytest.mark.parametrize('command', ['pwd','ls','ls -lah .','cat notes.txt','head -n 1 notes.txt',
                                     'tail -n 2 notes.txt','wc -lw notes.txt',"cat 'file with space'",
                                     'cat file\\ with\\ space','/bin/cat notes.txt'])
def test_readonly_literals_have_fixed_argv(snapshot, command):
    decision = classify_command(command,snapshot)
    assert decision.disposition == CommandDisposition.AUTO
    assert decision.argv[0].startswith(('/bin/','/usr/bin/'))
    assert all(value.startswith(snapshot.workspace) for value in decision.argv[1:] if '/' in value)


@pytest.mark.parametrize('command', ['ls && touch marker','cat notes.txt > marker','cat $(pwd)/notes.txt',
                                     'cat `pwd`/notes.txt','cat <(ls)','cat *.txt','X=1 ls','ls; pwd',
                                     'cat "$HOME/secret"','cat ~/.ssh/id_rsa','ls # comment',
                                     'find . -exec echo hi \\;','rg hello','tail -f notes.txt',
                                     'ls -L .','cat','cat -','python3 -c "print(1)"','curl https://example.test',
                                     'sudo ls','git status','ls\npwd','ls notes.txt -l'])
def test_complex_or_unknown_commands_require_confirmation(snapshot, command):
    assert classify_command(command,snapshot).disposition == CommandDisposition.CONFIRM


@pytest.mark.parametrize('command', ['', 'cat "unterminated', 'ls |', 'if true; then', 'cat\x00notes.txt',
                                     'sleep 1 &', 'sleep 1 & wait'])
def test_invalid_or_background_is_rejected(snapshot, command):
    assert classify_command(command,snapshot).disposition == CommandDisposition.INVALID


def test_syntax_validation_never_runs_expansion_or_bash_env(snapshot, monkeypatch):
    marker=Path(snapshot.workspace)/'marker'
    startup=Path(snapshot.workspace)/'startup.sh'
    startup.write_text(f'touch {shlex.quote(str(marker))}\n')
    monkeypatch.setenv('BASH_ENV',str(startup))
    decision = classify_command(f'ls $(touch {shlex.quote(str(marker))})', snapshot)
    assert decision.disposition == CommandDisposition.CONFIRM
    assert not marker.exists()


def test_paths_symlinks_and_special_files_require_confirmation(snapshot,tmp_path):
    outside=tmp_path/'outside'
    outside.write_text('secret')
    root=Path(snapshot.workspace)
    (root/'outside-link').symlink_to(outside)
    os.mkfifo(root/'pipe')
    for command in ['cat ../outside','cat outside-link','cat pipe','cat missing',f'cat {outside}']:
        assert classify_command(command,snapshot).disposition == CommandDisposition.CONFIRM
    (root/'inside-link').symlink_to(root/'notes.txt')
    decision=classify_command('cat inside-link',snapshot)
    assert decision.disposition == CommandDisposition.AUTO and decision.argv[-1] == str(root/'notes.txt')


def test_literal_quoted_shell_characters_are_not_expanded(snapshot):
    root=Path(snapshot.workspace)
    (root/'$(touch marker)').write_text('literal')
    result=BashExecutor()({'command':"cat '$(touch marker)'"},context(snapshot))
    assert json.loads(result.text)['stdout'] == 'literal'
    assert not (root/'marker').exists()


def test_confirm_all_requires_confirmation_even_pwd(snapshot):
    snapshot=replace(snapshot,policy=BashPolicy.CONFIRM_ALL)
    assert classify_command('pwd',snapshot).disposition == CommandDisposition.CONFIRM
    result=BashExecutor()({'command':'pwd'},context(snapshot))
    assert json.loads(result.text)['error_type'] == 'confirmation_unavailable'


def test_workspace_persistence_is_per_canonical_directory(tmp_db,snapshot,tmp_path):
    second=tmp_path/'second'
    second.mkdir()
    saved=replace(snapshot,policy=BashPolicy.CONFIRM_ALL)
    save_workspace_policy(saved)
    assert load_workspace_policy(snapshot.workspace) == BashPolicy.CONFIRM_ALL
    assert load_workspace_policy(second) == BashPolicy.READONLY_AUTO
    save_execution_preferences({'execution_workspace':snapshot.workspace, 'bash_policy':'confirm_all'})
    assert load_execution_preferences()['bash_policy'] == 'confirm_all'
    with pytest.raises(FrozenInstanceError):
        saved.workspace='changed'
    Path(snapshot.workspace).rename(tmp_path/'moved')
    Path(snapshot.workspace).mkdir()
    assert not snapshot.valid()


def test_environment_is_constructed_without_inherited_keys(snapshot,monkeypatch):
    for key in ['EXA_API_KEY','PARALLEL_API_KEY','BASH_ENV','PYTHONPATH','HTTP_PROXY']:
        monkeypatch.setenv(key,'test-secret')
    assert not set(snapshot.environment()) & {'EXA_API_KEY','PARALLEL_API_KEY','BASH_ENV','PYTHONPATH','HTTP_PROXY'}
    assert snapshot.environment()['HOME'] and snapshot.environment()['TMPDIR']


def test_confirmation_identity_cannot_be_changed_or_replayed(snapshot):
    ctx=context(snapshot,command='touch marker')
    broker=ConfirmationBroker()
    request=CommandConfirmation.create(ctx,'touch marker',30,'writes',clock=broker.clock)
    def answer(item):
        for forged in [replace(item,command='touch other'), replace(item,step_id='other'),
                       replace(item,tool_call_id='other'),replace(item,fingerprint='other'),
                       replace(item,workspace='/tmp')]:
            assert not broker.respond(forged,True)
        assert not broker.respond(item,1)
        assert broker.respond(item,True)
        assert not broker.respond(item,True)
    broker.on_request=answer
    assert broker.wait(request,ctx.cancelled) == ConfirmationOutcome.APPROVED
    assert not broker.pending and not broker.respond(request,True)
    with pytest.raises(ValueError):
        broker.wait(request,ctx.cancelled)


@pytest.mark.parametrize('outcome', ['denied','expired','cancelled'])
def test_unapproved_confirmation_has_no_side_effect(snapshot,outcome):
    now=[0.0]
    broker=ConfirmationBroker(clock=lambda:now[0])
    ctx=context(snapshot)
    def answer(request):
        if outcome == 'denied':
            broker.respond(request,False)
        elif outcome == 'expired':
            now[0]=600
            assert not broker.respond(request,True)
        else:
            ctx.cancelled.set()
    broker.on_request=answer
    result=BashExecutor(broker)({'command':'touch marker'},ctx)
    assert json.loads(result.text)['error_type'] == 'confirmation_'+outcome
    assert not (Path(snapshot.workspace)/'marker').exists() and not broker.pending


def test_approved_command_and_workspace_change_are_checked_before_spawn(snapshot,tmp_path):
    broker=ConfirmationBroker()
    broker.on_request=lambda request:broker.respond(request,True)
    result=BashExecutor(broker)({'command':'printf approved > marker'},context(snapshot))
    assert not result.error and (Path(snapshot.workspace)/'marker').read_text() == 'approved'
    def replace_workspace(request):
        Path(snapshot.workspace).rename(tmp_path/'old')
        Path(snapshot.workspace).mkdir()
        broker.respond(request,True)
    broker.on_request=replace_workspace
    result=BashExecutor(broker)({'command':'touch replacement'},context(snapshot))
    assert json.loads(result.text)['error_type'] == 'workspace_changed'
    assert not (Path(snapshot.workspace)/'replacement').exists()


def test_confirmation_pause_does_not_extend_command_timeout(snapshot):
    now=[10.0]
    budget=ActiveBudget(300,clock=lambda:now[0])
    original=budget.deadline
    with budget.pause():
        now[0]+=601
        assert budget.deadline == original+601
    assert budget.deadline == original+601
    now[0]+=300
    assert now[0] == budget.deadline


def test_expired_wait_returns_paired_error_then_run_continues(snapshot):
    now=[0.0]
    broker=ConfirmationBroker(clock=lambda:now[0])
    broker.on_request=lambda request:now.__setitem__(0,601)
    request=ChatClient().create_request([Message('user','task')],
                                      agent_id='general_assistant',think=False,
                                      options={'num_ctx':8192,'num_predict':1024})
    steps=[]
    def model(req,payload,deadline):
        steps.append(payload)
        turn=(ModelTurn('', '', (ToolCall('call','bash','{"command":"touch marker"}'),), 'stop')
              if len(steps)==1 else ModelTurn('No command executed.','',(),'stop'))
        return ChatResult(req.request_id,ResultStatus.SUCCEEDED,turn=turn,run_id=req.run_id,
                          step_id=req.step_id,conversation_id=req.conversation_id)
    run=RunContext.create(request,tools=(ToolSpec.create('bash',BashExecutor(broker)),),
                          tools_admitted=True,execution=snapshot,limits=RunLimits())
    result=RunLoop(run,threading.Event(),model).run(_payload(request,True))
    assert result.ok and len(steps)==2
    assert 'confirmation_expired' in steps[1]['messages'][-1]['content']
    assert not (Path(snapshot.workspace)/'marker').exists()


def test_stdout_stderr_exit_code_and_noninteractive_stdin(snapshot):
    ctx=context(snapshot)
    command='import sys; print(sys.stdin.read()); print("err",file=sys.stderr); exit(4)'
    result=run_command((sys.executable,'-c',command),
                       snapshot,ctx,3,execution_mode='argv')
    assert result.exit_code == 4 and result.stdout == b'\n' and result.stderr == b'err\n'
    assert result.tool_output().error


def test_timeout_and_continuous_output_are_bounded(snapshot):
    ctx=context(snapshot)
    result=run_command(('/bin/sleep','5'),snapshot,ctx,0.1,execution_mode='argv')
    assert result.error_type == 'timeout' and result.duration < 2
    result=run_command((sys.executable,'-c','import os; os.write(1,b"x"*2000000)'),snapshot,ctx,3,execution_mode='argv')
    assert result.error_type == 'output_limit' and result.truncated and len(result.stdout) <= 32768
    data=json.loads(result.tool_output().text)
    assert data['truncated'] and len(result.tool_output().text.encode()) <= 4096


def test_control_character_output_keeps_valid_json_under_result_budget():
    output=BashResult(0,b'\x00'*32768,b'\x01'*32768).tool_output()
    assert len(output.text.encode()) <= 4096 and json.loads(output.text)['truncated']


def test_cancel_cleans_child_process_group(snapshot):
    ctx=context(snapshot)
    pid_file=Path(snapshot.workspace)/'child.pid'
    program=('import subprocess,sys,time,pathlib,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
             'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
             'pathlib.Path("child.pid").write_text(str(p.pid)); time.sleep(30)')
    def cancel_after_child():
        end=time.monotonic()+3
        while not pid_file.exists() and time.monotonic()<end:
            time.sleep(.01)
        ctx.cancelled.set()
    thread=threading.Thread(target=cancel_after_child)
    thread.start()
    result=run_command((sys.executable,'-c',program),snapshot,ctx,5,execution_mode='argv')
    thread.join(4)
    assert result.error_type == 'cancelled' and result.duration < 3
    child=int(pid_file.read_text())
    end=time.monotonic()+2
    while time.monotonic()<end:
        try:
            os.kill(child,0)
        except ProcessLookupError:
            break
        time.sleep(.02)
    else:
        pytest.fail('Child process survived cancellation')


def test_worker_tool_round_runs_real_readonly_executor(qtbot,ollama_server,snapshot):
    from ai_desktop.llm.run_worker import RunWorker
    from ai_desktop.utils.storage import Message
    worker=RunWorker([Message('user','read notes')], '',agent_id='general_assistant',think=False,
                     options={'num_ctx':8192,'num_predict':1024},tools_admitted=True,
                     execution=snapshot,confirmations=ConfirmationBroker())
    ollama_server.enqueue({'message':{'tool_calls':[{'function':{'name':'bash',
                           'arguments':{'command':'cat notes.txt'}}}]},'done':True})
    ollama_server.enqueue({'message':{'content':'read complete'},'done':True})
    with qtbot.waitSignal(worker.done,timeout=3000) as signal:
        worker.start()
    assert worker.wait(1000) and signal.args[0].ok
    tool=json.loads(ollama_server.requests[1]['payload']['messages'][-1]['content'])
    assert tool['stdout'] == 'hello\nworld\n' and tool['execution_mode'] == 'argv'
    worker.deleteLater()


def test_run_activity_budget_excludes_confirmation_wait(snapshot):
    now=[0.0]
    deadlines=[]
    request=ChatClient().create_request([Message('user','task')],agent_id='general_assistant',think=False,
                                      options={'num_ctx':8192,'num_predict':1024})
    def executor(args,ctx):
        with ctx.budget.pause():
            now[0]+=601
        return ToolOutput('user did not confirm',True)
    def model(req,payload,deadline):
        deadlines.append(deadline)
        turn=(ModelTurn('', '', (ToolCall('c','bash','{"command":"touch marker"}'),), 'stop')
              if len(deadlines)==1 else ModelTurn('done','',(),'stop'))
        return ChatResult(req.request_id,ResultStatus.SUCCEEDED,turn=turn,run_id=req.run_id,
                          step_id=req.step_id,conversation_id=req.conversation_id)
    ctx=RunContext.create(request,tools=(ToolSpec.create('bash',executor),),tools_admitted=True,execution=snapshot)
    result=RunLoop(ctx,threading.Event(),model,clock=lambda:now[0]).run(_payload(request,True))
    assert result.ok and deadlines == [300,901]


def test_settings_workspace_policy_switch_and_focus_preserve_choice(qtbot,tmp_db,snapshot,tmp_path):
    from ai_desktop.settings_manager import SettingsManager
    from ai_desktop.ui.settings_dialog import SettingsDialog
    save_workspace_policy(replace(snapshot,policy=BashPolicy.CONFIRM_ALL))
    current=SettingsManager().current()
    dialog=SettingsDialog(current)
    qtbot.addWidget(dialog)
    assert not dialog._widgets['bash_policy'].isEnabled()
    dialog._widgets['execution_workspace'].setText(snapshot.workspace)
    dialog._workspace_changed()
    assert dialog._widgets['bash_policy'].currentData() == 'confirm_all'
    dialog._widgets['bash_policy'].setCurrentIndex(dialog._widgets['bash_policy'].findData('readonly_auto'))
    dialog._workspace_changed()  # focus movement with the same path must not undo the edit
    assert dialog._widgets['bash_policy'].currentData() == 'readonly_auto'
    with qtbot.waitSignal(dialog.settings_applied,timeout=1000) as signal:
        dialog._on_save()
    SettingsManager().apply(signal.args[0])
    assert load_workspace_policy(snapshot.workspace) == BashPolicy.READONLY_AUTO
    second=tmp_path/'other'
    second.mkdir()
    assert load_workspace_policy(second) == BashPolicy.READONLY_AUTO


def test_invalid_workspace_preferences_cannot_replace_valid_saved_choice(tmp_db,snapshot):
    from ai_desktop.settings_manager import SettingsManager
    manager=SettingsManager()
    manager.apply({'execution_workspace':snapshot.workspace,'bash_policy':'confirm_all'})
    assert 'execution_workspace' not in manager.apply({'execution_workspace':'/no-such-h2-workspace',
                                                      'bash_policy':'readonly_auto'})
    assert manager.current()['execution_workspace'] == snapshot.workspace
    assert manager.current()['bash_policy'] == 'confirm_all'


def test_execution_runtime_probe_only_reads_its_temporary_fixture():
    from ai_desktop.services.bash_executor import probe_execution_runtime
    assert probe_execution_runtime() == {'engine':'bash','status':'ready','automatic_mode':'fixed_argv'}


@pytest.mark.parametrize('command', ['echo message >&2','ls # & ignored', 'cat <<EOF\ntext & text\nEOF'])
def test_non_background_ampersands_are_not_malformed(snapshot,command):
    assert classify_command(command,snapshot).disposition == CommandDisposition.CONFIRM
