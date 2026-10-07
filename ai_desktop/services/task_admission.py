"""Fresh selected-model capability checks and per-conversation tool authorization."""
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ai_desktop.llm.model_options import global_options
from ai_desktop.llm.run_types import RunLimits, chat_tools_eligible
from ai_desktop.llm.thinking import ThinkSetting, normalize_think, parse_thinking, resolve_think
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.services.web_search import SearchSettings

TASK_MODEL = 'qwen3.5:9b-mlx'
TASK_DIGEST = '203e30078279db51132b9e026ceb7bb21330e5b1af67ef190671b375c9770404'
TASK_SERVICE_VERSION = '0.34.3'
# Historical H0 evidence only. Product requests use TaskModelSettings instead.
TASK_OPTIONS = (('num_ctx', 8192), ('num_predict', 1024), ('temperature', 0))


@dataclass(frozen=True)
class TaskModelSettings:
    """Effective settings frozen before discovery, shared by preview and execution."""
    options: tuple[tuple[str, int | float], ...]
    think_setting: ThinkSetting
    think_source: str
    profile_name: str = ''

    @classmethod
    def from_config(cls, resolved=None):
        from ai_desktop import config
        options = global_options()
        if resolved is not None:
            options.update(resolved.options)
        return cls(tuple(options.items()),
                   resolved.think_setting if resolved else normalize_think(config.OLLAMA_THINK),
                   resolved.think_source if resolved else '全局设置',
                   resolved.profile_name if resolved else '')

    def summary(self, model):
        options = dict(self.options)
        return (f'{model}\n上下文 {options["num_ctx"]} · 输出 {options["num_predict"]}（含思考 token）\n'
                f'思考：{self.think_setting.label}（{self.think_source}）\n'
                f'温度 {options["temperature"]:g} · Top P {options["top_p"]:g} · '
                f'Top K {options["top_k"]} · 重复惩罚 {options["repeat_penalty"]:g}\n'
                f'参数来源：{self.profile_name or "全局设置"}。角色专用配置可覆盖思考、温度和输出；'
                '上下文窗口使用全局设置，工具模型跟随顶栏。')


def local_service_url(value):
    parts = urlsplit(str(value).strip())
    try:
        port = parts.port
    except ValueError:
        raise ValueError('工具任务需要有效的本机 Ollama 地址。') from None
    if (parts.scheme != 'http' or parts.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parts.username is not None or parts.password is not None or parts.path not in {'', '/'}
            or parts.query or parts.fragment or port == 0):
        raise ValueError('当前工具配置仅准入本机 HTTP Ollama 服务。')
    return str(value).strip().rstrip('/')


@dataclass(frozen=True)
class TaskAdmission:
    base_url: str
    checked_at: float
    model: str = TASK_MODEL
    digest: str = TASK_DIGEST
    service_version: str = TASK_SERVICE_VERSION
    vision_supported: bool = True
    think: bool | str | None = None
    settings: TaskModelSettings = field(default_factory=TaskModelSettings.from_config)
    warnings: tuple[str, ...] = ()

    def valid(self, base_url, model=None):
        return (self.base_url == local_service_url(base_url)
                and bool(self.model and self.digest and self.service_version)
                and (model is None or model == self.model)
                and 0 <= time.monotonic() - self.checked_at <= 60)

    def record(self):
        return {'model': self.model, 'digest': self.digest, 'service_version': self.service_version,
                'evidence': 'selected-model-capabilities',
                'budget_verified': (self.model, self.digest, self.service_version) ==
                                   (TASK_MODEL, TASK_DIGEST, TASK_SERVICE_VERSION)
                                   and dict(self.settings.options) == dict(TASK_OPTIONS) and self.think is False,
                'tool_use_admitted': True, 'think': self.think,
                'options': dict(self.settings.options),
                'requested_think': self.settings.think_setting.record(),
                'think_source': self.settings.think_source, 'warnings': list(self.warnings)}


def validate_discovery(base_url, version, tags, show, *, model=TASK_MODEL, settings=None):
    base_url = local_service_url(base_url)
    settings = settings or TaskModelSettings.from_config()
    if not isinstance(model, str) or not model.strip() or len(model) > 256 or model.strip() != model:
        raise ValueError('请选择有效的工具任务模型。')
    service_version = version.get('version') if isinstance(version, dict) else None
    if not isinstance(service_version, str) or not service_version.strip():
        raise ValueError('Ollama 版本响应无效，未启用工具。')
    models = tags.get('models') if isinstance(tags, dict) else None
    if not isinstance(models, list):
        raise ValueError('模型列表无效，未启用工具。')
    matches = [item for item in models if isinstance(item, dict) and item.get('name') == model]
    if len(matches) != 1 or not isinstance(matches[0].get('digest'), str) or not matches[0]['digest'].strip():
        raise ValueError(f'所选模型 {model} 未安装或身份信息无效，未启用工具。')
    capabilities = show.get('capabilities') if isinstance(show, dict) else None
    if not isinstance(capabilities, list) or 'tools' not in capabilities or 'completion' not in capabilities:
        raise ValueError(f'所选模型 {model} 未声明工具调用能力，未启用工具。')
    context_limits = [value for key, value in show.get('model_info', {}).items()
                      if key.endswith('.context_length') and type(value) is int] \
                     if isinstance(show.get('model_info'), dict) else []
    requested_context = dict(settings.options)['num_ctx']
    requested_output = dict(settings.options)['num_predict']
    if requested_output + RunLimits().context_reserve >= requested_context:
        raise ValueError(f'工具任务的输出上限 {requested_output} token 需小于上下文窗口 '
                         f'{requested_context} token，并为输入和工具结果保留空间。'
                         '请增大上下文窗口或调小输出上限。')
    if context_limits and min(context_limits) < requested_context:
        raise ValueError(f'所选模型 {model} 声明的上下文上限为 {min(context_limits)} token，'
                         f'当前配置为 {requested_context} token。请调小上下文窗口或切换模型。')
    think, warnings = resolve_think(settings.think_setting, parse_thinking(show))
    return TaskAdmission(base_url, time.monotonic(), model=model, digest=matches[0]['digest'],
                         service_version=service_version, vision_supported='vision' in capabilities,
                         think=think, settings=settings, warnings=warnings)


@dataclass(frozen=True)
class TaskAuthorization:
    execution: ExecutionSnapshot | None = None
    search: SearchSettings | None = None
    agent_id: str = 'general_assistant'

    def __post_init__(self):
        if not chat_tools_eligible(self.agent_id, 'chat'):
            raise ValueError('工具授权需要有效的对话 Agent。')
        if self.execution is None and self.search is None:
            raise ValueError('请至少选择一个工具。')
        if self.execution is not None and not isinstance(self.execution, ExecutionSnapshot):
            raise ValueError('工作区配置无效。')
        if self.search is not None and not isinstance(self.search, SearchSettings):
            raise ValueError('搜索配置无效。')

    def worker_kwargs(self, admission, base_url, *, agent_id, origin, model=None, settings=None):
        from ai_desktop import config
        if not config.CHAT_TOOLS_ENABLED:
            raise ValueError('设置中已关闭对话工具，请在工具执行页重新启用。')
        if not chat_tools_eligible(agent_id, origin):
            raise ValueError('快捷动作或无效角色不能使用工具。')
        if agent_id != self.agent_id:
            raise ValueError('工具授权属于另一个 Agent，请为当前角色重新启用。')
        if not isinstance(admission, TaskAdmission) or not admission.valid(base_url, model):
            raise ValueError('模型准入检查已失效，请重新检查后发送。')
        if settings is not None and admission.settings != settings:
            raise ValueError('模型参数已变化，请重新检查后发送。')
        if self.execution is not None and not self.execution.valid():
            raise ValueError('工具工作区已变化，请重新选择工作目录。')
        return {'model': admission.model, 'think': admission.think,
                'think_setting': admission.settings.think_setting,
                'think_source': admission.settings.think_source,
                'options': dict(admission.settings.options), 'exact_options': True,
                'tools_admitted': True,
                'execution': self.execution, 'search_settings': self.search}

    def record(self):
        return {'agent_id': self.agent_id, 'execution': self.execution.record() if self.execution else None,
                'search': self.search.record() if self.search else None}
