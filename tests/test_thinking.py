"""Exact thinking domains, legacy settings, request snapshots and async controls."""
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from ai_desktop import config
from ai_desktop.llm.chat_client import ChatClient, _payload
from ai_desktop.llm.service_checks import AsyncServiceChecks, ImageCapability, ModelCapabilityResult
from ai_desktop.llm.thinking import (
    ThinkingCapability,
    ThinkMode,
    ThinkSetting,
    cache_thinking,
    load_cached_thinking,
    normalize_think,
    parse_thinking,
    resolve_think,
)
from ai_desktop.services.model_profiles import ModelProfile, ModelProfileManager
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.utils import storage
from ai_desktop.utils.storage import Message

BOOL = ThinkingCapability((False, True), True, True)
LEVELS = ThinkingCapability(('low', 'medium', 'xhigh'), 'medium', True)


@pytest.mark.parametrize('old, mode', [(None, ThinkMode.INHERIT), (True, ThinkMode.ON), (False, ThinkMode.OFF)])
def test_profile_legacy_json_migrates_without_database_change(tmp_db, old, mode):
    storage.save_model_profile_record({'id':'legacy', 'name':'旧配置', 'options':json.dumps({'think':old}),
                                      'updated_at':1})
    manager = ModelProfileManager()
    profile = manager.profiles[0]
    assert profile.think == ThinkSetting(mode)
    manager.save(profile)
    assert ModelProfileManager().profiles[0].think == profile.think
    record = json.loads(storage.list_model_profile_records()[0]['options'])
    assert record['think']['mode'] == mode.value


@pytest.mark.parametrize('old, mode', [('True', ThinkMode.ON), ('False', ThinkMode.OFF),
                                      ('{"mode":"model_default"}', ThinkMode.MODEL_DEFAULT),
                                      ('{"mode":"named","level":"xhigh"}', ThinkMode.NAMED)])
def test_global_legacy_and_structured_load(tmp_db, monkeypatch, old, mode):
    monkeypatch.setattr(config, 'OLLAMA_THINK', ThinkSetting())
    storage.save_setting('ollama_think', old)
    SettingsManager().load()
    assert config.OLLAMA_THINK.mode == mode


def test_global_save_round_trip_and_reject_self_inheritance(tmp_db, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', ThinkSetting())
    manager = SettingsManager()
    assert manager.apply({'think':{'mode':'named','level':'custom-effort'}}) == ['think']
    assert json.loads(storage.get_setting('ollama_think')) == {'mode':'named','level':'custom-effort'}
    assert manager.apply({'think':{'mode':'inherit'}}) == []
    SettingsManager().load()
    assert config.OLLAMA_THINK == ThinkSetting(ThinkMode.NAMED, 'custom-effort')


@pytest.mark.parametrize('raw', ['1', '"high"', '{"mode":"bad"}', '{"mode":"inherit"}'])
def test_corrupt_global_uses_model_default(tmp_db, monkeypatch, raw):
    monkeypatch.setattr(config, 'OLLAMA_THINK', ThinkSetting(ThinkMode.ON))
    storage.save_setting('ollama_think', raw)
    SettingsManager().load()
    assert config.OLLAMA_THINK == ThinkSetting()


@pytest.mark.parametrize('values, default', [([False,True],True), ([False],False),
                                           (['low','xhigh'],'xhigh'), ([False,'high'],'high')])
def test_parse_exact_mixed_and_boolean_domains(values, default):
    capability = parse_thinking({'thinking':{'values':values,'default':default}})
    assert capability.known and list(capability.values) == values and capability.default == default
    assert not capability.supports(1)


@pytest.mark.parametrize('metadata', [None, {}, {'values':[]}, {'values':[1]}, {'values':[True,True]},
                                     {'values':['high'],'default':'High'}, {'values':[' high ']},
                                     {'values':[False],'default':True}])
def test_malformed_metadata_is_unknown(metadata):
    assert not parse_thinking({'thinking':metadata}).known
    assert not parse_thinking({}).known


def test_inherit_and_model_default_have_different_wire_resolution(tmp_db, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', ThinkSetting(ThinkMode.ON))
    manager = ModelProfileManager()
    manager.save(ModelProfile('inherit', '继承'))
    manager.save(ModelProfile('default', '默认', think=ThinkSetting()))
    inherited = manager.resolve(global_model='model', agent_profile_id='inherit', thinking_capability=BOOL)
    explicit = manager.resolve(global_model='model', agent_profile_id='default', thinking_capability=BOOL)
    assert inherited.think is True and inherited.think_source == '全局设置'
    assert explicit.think is None and explicit.think_source == '默认'
    assert explicit.think_setting.mode == ThinkMode.MODEL_DEFAULT


def test_named_value_is_preserved_and_validated_on_final_model(tmp_db, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', True)
    manager = ModelProfileManager()
    setting = ThinkSetting(ThinkMode.NAMED, 'xhigh')
    manager.save(ModelProfile('levels', '档位', model='named-model', think=setting))
    result = manager.resolve(global_model='bool-model', agent_profile_id='levels',
                             thinking_capability=lambda model: LEVELS if model == 'named-model' else BOOL)
    assert result.think == 'xhigh' and result.think_source == '档位'
    request = ChatClient(model=result.model).create_request([Message('user','hello')], think=result.think,
                                                          think_setting=result.think_setting,
                                                          think_source=result.think_source)
    assert _payload(request, True)['think'] == 'xhigh'
    assert request.think_setting == setting and request.think_source == '档位'
    switched = result.for_model('bool-model', BOOL)
    assert switched.think is None and switched.warnings and switched.think_setting == setting


@pytest.mark.parametrize('setting, capability', [(ThinkSetting(ThinkMode.ON),ThinkingCapability((False,),False,True)),
                                                (ThinkSetting(ThinkMode.OFF),LEVELS),
                                                (ThinkSetting(ThinkMode.NAMED,'High'),LEVELS),
                                                (ThinkSetting(ThinkMode.NAMED,'xhigh'),ThinkingCapability())])
def test_unsupported_is_visible_model_default(setting, capability):
    value, warnings = resolve_think(setting, capability)
    assert value is None and '模型默认' in warnings[0]


def test_explicit_null_does_not_inherit_global(tmp_db, monkeypatch):
    monkeypatch.setattr(config, 'OLLAMA_THINK', True)
    request = ChatClient().create_request([Message('user','hello')], think=None)
    assert _payload(request, True)['think'] is None
    assert request.think_setting == ThinkSetting()
    with pytest.raises(ValueError):
        ChatClient().create_request([], think=1)
    with pytest.raises(ValueError):
        normalize_think({'mode':'on','level':'high'})


def test_capability_cache_requires_same_endpoint_model_digest(tmp_db):
    cache_thinking('http://service/', 'model', 'v1', LEVELS)
    assert load_cached_thinking('http://service', 'model', 'v1') == LEVELS
    assert not load_cached_thinking('http://other', 'model', 'v1').known
    assert not load_cached_thinking('http://service', 'other-model', 'v1').known
    assert not load_cached_thinking('http://service', 'model', 'v2').known
    cache_thinking('http://service', 'model', 'v1', ThinkingCapability())
    assert not load_cached_thinking('http://service', 'model', 'v1').known


def test_api_show_parses_thinking_without_second_request(qtbot, ollama_server, tmp_db):
    checker = AsyncServiceChecks()
    ollama_server.enqueue(chunks=[json.dumps({'capabilities':['completion'],
                                            'thinking':{'values':['low','custom'],'default':'custom'}}).encode()])
    with qtbot.waitSignal(checker.model_capability_checked, timeout=1000) as signal:
        checker.check_model_capability(ollama_server.url, 'model', 'digest')
    result = signal.args[0]
    assert result.thinking.values == ('low','custom') and result.thinking.default == 'custom'
    assert len(ollama_server.requests) == 1
    checker.cancel_all()
    checker.deleteLater()


def test_selector_domains_reject_stale_result_and_cancel_on_done(qtbot, tmp_db):
    from ai_desktop.ui.model_profile_dialog import _ModelProfileEditDialog
    cache_thinking('http://model-host', 'one', 'v1', BOOL)
    cache_thinking('http://model-host', 'two', 'v2', LEVELS)
    dialog = _ModelProfileEditDialog('编辑', ModelProfile('profile','配置','one',False), ['one','two'],
                                    base_url='http://model-host', global_model='one',
                                    model_versions={'one':'v1','two':'v2'})
    qtbot.addWidget(dialog)
    selector=dialog._think
    assert [selector.combo.itemData(i).mode for i in range(selector.combo.count())] == [
        ThinkMode.INHERIT, ThinkMode.MODEL_DEFAULT, ThinkMode.OFF, ThinkMode.ON]
    assert dialog.profile().think == ThinkSetting(ThinkMode.OFF)
    dialog._model.setCurrentIndex(dialog._model.findData('two'))
    assert selector.currentData() == ThinkSetting()
    assert '已改为模型默认' in selector.hint.text()
    assert [selector.combo.itemData(i).level for i in range(2,selector.combo.count())] == ['low','medium','xhigh']
    selector._sequence=3
    selector._checked(ModelCapabilityResult(2,'http://model-host','one','v1',ImageCapability.SUPPORTED,thinking=BOOL))
    assert selector.capability == LEVELS
    selector.set_setting(ThinkSetting(ThinkMode.NAMED,'xhigh'))
    assert dialog.profile().think.level == 'xhigh'
    with patch.object(selector._checks,'cancel_all') as cancel:
        dialog.reject()
    cancel.assert_called_once()
    assert not selector._refresh_timer.isActive()


def test_selector_unknown_keeps_saved_intent_but_invents_no_choices(qtbot,tmp_db):
    from ai_desktop.ui.thinking_selector import ThinkingSelector
    selector=ThinkingSelector(ThinkSetting(ThinkMode.NAMED,'custom'),allow_inherit=True)
    qtbot.addWidget(selector)
    assert selector.currentData().level == 'custom'
    assert selector.combo.count() == 3
    assert '待核验' in selector.combo.currentText() and '模型默认' in selector.hint.text()
    selector.stop()


def test_global_ui_serializes_mode_without_inherit(qtbot,tmp_db):
    from ai_desktop.ui.settings_dialog import SettingsDialog
    dialog=SettingsDialog({'think':False}, model='model')
    qtbot.addWidget(dialog)
    selector=dialog._widgets['think']
    selector.capability=ThinkingCapability((False,),False,True)
    selector.set_setting(False)
    assert [selector.combo.itemData(i).mode for i in range(selector.combo.count())] == [
        ThinkMode.MODEL_DEFAULT,ThinkMode.OFF]
    assert dialog._collect()['think'] == {'mode':'off'}
    selector.set_setting(ThinkSetting())
    assert dialog._collect()['think'] == {'mode':'model_default'}
    dialog.reject()


def test_runloop_keeps_named_value_across_steps(tmp_db):
    import threading

    from ai_desktop.llm.events import ChatResult, ResultStatus
    from ai_desktop.llm.ollama_protocol import ModelTurn, ToolCall
    from ai_desktop.llm.run_loop import RunLoop
    from ai_desktop.llm.run_types import RunContext, ToolOutput, ToolSpec

    request = ChatClient().create_request([Message('user', 'search')], think='xhigh',
                                         agent_id='general_assistant', options={'num_predict':1024})
    seen = []
    calls = []
    def execute(arguments, context):
        calls.append(arguments)
        return ToolOutput('synthetic result')
    def step(request, payload, deadline):
        seen.append(payload['think'])
        turn = (ModelTurn('', '', (ToolCall('local', 'web_search', '{"query":"example"}'),), 'stop')
                if len(seen) == 1 else ModelTurn('ok', '', (), 'stop'))
        return ChatResult(request.request_id, ResultStatus.SUCCEEDED, turn=turn,
                          run_id=request.run_id, step_id=request.step_id, conversation_id=request.conversation_id)
    context = RunContext.create(request, tools=(ToolSpec.create('web_search', execute),), tools_admitted=True)
    result = RunLoop(context, threading.Event(), step).run(_payload(request, True))
    assert result.ok and seen == ['xhigh', 'xhigh'] and calls == [{'query':'example'}]
    assert replace(request, think=None).think is None


def test_selector_switch_aborts_old_http_and_reads_new_domain(qtbot, tmp_db, ollama_server):
    from ai_desktop.ui.thinking_selector import ThinkingSelector
    selector = ThinkingSelector(False, allow_inherit=True)
    qtbot.addWidget(selector)
    old = ollama_server.enqueue(before_headers=True)
    selector.set_model(ollama_server.url, 'old', 'v1')
    qtbot.waitUntil(old.received.is_set, timeout=1500)
    ollama_server.enqueue(chunks=[json.dumps({'capabilities':['completion'],
                                            'thinking':{'values':['custom'],'default':'custom'}}).encode()])
    selector.set_model(ollama_server.url, 'new', 'v2')
    qtbot.waitUntil(lambda: selector.capability.known, timeout=1500)
    qtbot.waitUntil(old.disconnected.is_set, timeout=1000)
    assert selector.capability.values == ('custom',)
    assert selector.currentData() == ThinkSetting()
    assert load_cached_thinking(ollama_server.url,'new','v2').values == ('custom',)
    assert not load_cached_thinking(ollama_server.url,'old','v1').known
    selector.stop()


def test_worker_explicit_default_stays_null_in_real_http(qtbot, tmp_db, ollama_server, monkeypatch):
    from ai_desktop.llm.run_worker import RunWorker
    monkeypatch.setattr(config,'OLLAMA_THINK',ThinkSetting(ThinkMode.ON))
    worker = RunWorker([Message('user','hello')], '', think=None,
                                 think_setting=ThinkSetting(), think_source='默认配置')
    ollama_server.enqueue({'message':{'content':'ok'},'done':True})
    with qtbot.waitSignal(worker.done,timeout=1500) as signal:
        worker.start()
    assert worker.wait(1000) and signal.args[0].ok
    assert ollama_server.requests[0]['payload']['think'] is None
    assert worker.request.think_setting.mode == ThinkMode.MODEL_DEFAULT
    assert worker.request.think_source == '默认配置'
    worker.deleteLater()
