"""Thinking settings, exact model domains and version-scoped discovery cache."""
import json
from dataclasses import dataclass
from enum import Enum


class ThinkMode(str, Enum):
    INHERIT = 'inherit'
    MODEL_DEFAULT = 'model_default'
    OFF = 'off'
    ON = 'on'
    NAMED = 'named'


@dataclass(frozen=True)
class ThinkSetting:
    mode: ThinkMode = ThinkMode.MODEL_DEFAULT
    level: str | None = None

    def __post_init__(self):
        if not isinstance(self.mode, ThinkMode):
            raise ValueError('思考选项的模式无效。')
        if self.mode == ThinkMode.NAMED:
            if (not isinstance(self.level, str) or not self.level or len(self.level) > 100
                    or self.level.strip() != self.level):
                raise ValueError('思考选项需要精确的档位名称。')
            if any(ord(char) < 32 for char in self.level):
                raise ValueError('思考选项的档位不能包含控制字符。')
        elif self.level is not None:
            raise ValueError('只有命名思考档位可以携带名称。')

    def record(self):
        value = {'mode': self.mode.value}
        if self.level is not None:
            value['level'] = self.level
        return value

    @property
    def label(self):
        return {ThinkMode.INHERIT: '继承全局设置', ThinkMode.MODEL_DEFAULT: '模型默认',
                ThinkMode.OFF: '关闭', ThinkMode.ON: '开启', ThinkMode.NAMED: self.level}[self.mode]

    def wire(self):
        if self.mode == ThinkMode.INHERIT:
            raise ValueError('继承选项不能直接发给模型。')
        return {ThinkMode.MODEL_DEFAULT: None, ThinkMode.OFF: False,
                ThinkMode.ON: True, ThinkMode.NAMED: self.level}[self.mode]


def normalize_think(value, *, allow_inherit=False) -> ThinkSetting:
    """Legacy profile null means inherit; global null means model default."""
    if isinstance(value, ThinkSetting):
        setting = value
    elif value is None:
        setting = ThinkSetting(ThinkMode.INHERIT if allow_inherit else ThinkMode.MODEL_DEFAULT)
    elif type(value) is bool:
        setting = ThinkSetting(ThinkMode.ON if value else ThinkMode.OFF)
    elif isinstance(value, dict) and set(value) <= {'mode', 'level'}:
        try:
            setting = ThinkSetting(ThinkMode(value['mode']), value.get('level'))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('思考选项无效。') from exc
    else:
        raise ValueError('思考选项必须是结构化模式、旧布尔值或继承。')
    if setting.mode == ThinkMode.INHERIT and not allow_inherit:
        raise ValueError('全局思考选项不能继承自身。')
    return setting


def load_think_setting(raw: str, *, allow_inherit=False):
    if raw.lower() in {'true', 'false'}:
        return normalize_think(raw.lower() == 'true', allow_inherit=allow_inherit)
    return normalize_think(json.loads(raw), allow_inherit=allow_inherit)


@dataclass(frozen=True)
class ThinkingCapability:
    values: tuple[bool | str, ...] = ()
    default: bool | str | None = None
    known: bool = False
    error: str = ''

    def supports(self, value):
        return self.known and any(type(value) is type(candidate) and value == candidate for candidate in self.values)

    def record(self):
        return {'values': list(self.values), 'default': self.default, 'known': self.known}


def parse_thinking(data: dict) -> ThinkingCapability:
    if 'thinking' not in data:
        return ThinkingCapability(error='服务未声明思考控制；使用模型默认。')
    metadata = data['thinking']
    if not isinstance(metadata, dict) or not isinstance(metadata.get('values'), list) or not metadata['values']:
        return ThinkingCapability(error='思考能力声明无效；使用模型默认。')
    values = []
    try:
        for value in metadata['values']:
            if type(value) is bool:
                pass
            elif isinstance(value, str):
                ThinkSetting(ThinkMode.NAMED, value)
            else:
                raise ValueError('invalid value')
            if any(type(value) is type(item) and value == item for item in values):
                raise ValueError('duplicate value')
            values.append(value)
        default = metadata.get('default')
        if default is not None and not any(type(default) is type(item) and default == item for item in values):
            raise ValueError('invalid default')
    except ValueError:
        return ThinkingCapability(error='思考能力声明无效；使用模型默认。')
    return ThinkingCapability(tuple(values), default, True)


def resolve_think(setting, capability: ThinkingCapability | None):
    setting = normalize_think(setting)
    if setting.mode == ThinkMode.MODEL_DEFAULT:
        return None, ()
    value = setting.wire()
    if capability is not None and capability.supports(value):
        return value, ()
    reason = '尚未确认思考能力' if capability is None or not capability.known else '不支持此思考选项'
    return None, (f'模型{reason}（{setting.label}），本次使用模型默认。',)


def thinking_cache_key(base_url, model, version):
    return 'cached_thinking_capability:' + json.dumps([str(base_url).strip().rstrip('/'), model, version],
                                                     ensure_ascii=False, separators=(',', ':'))


def load_cached_thinking(base_url, model, version):
    from ai_desktop.utils.storage import get_setting
    raw = get_setting(thinking_cache_key(base_url, model, version))
    if not raw:
        return ThinkingCapability()
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get('known') is not True:
            return ThinkingCapability()
        return parse_thinking({'thinking': record})
    except (ValueError, TypeError):
        return ThinkingCapability()


def cache_thinking(base_url, model, version, capability):
    from ai_desktop.utils.storage import save_setting
    # Also invalidate an older declaration if the server now omits metadata.
    save_setting(thinking_cache_key(base_url, model, version),
                 json.dumps(capability.record(), ensure_ascii=False, separators=(',', ':')))
