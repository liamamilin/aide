"""Fresh selected-model capability checks and per-conversation tool authorization."""
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from ai_desktop.llm.thinking import ThinkMode, ThinkSetting, parse_thinking
from ai_desktop.services.execution_context import ExecutionSnapshot
from ai_desktop.services.web_search import SearchSettings

TASK_MODEL = 'qwen3.5:9b-mlx'
TASK_DIGEST = '203e30078279db51132b9e026ceb7bb21330e5b1af67ef190671b375c9770404'
TASK_SERVICE_VERSION = '0.34.3'
TASK_OPTIONS = (('num_ctx', 8192), ('num_predict', 1024), ('temperature', 0))


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
    think: bool | None = False

    def valid(self, base_url, model=None):
        return (self.base_url == local_service_url(base_url)
                and bool(self.model and self.digest and self.service_version)
                and (model is None or model == self.model)
                and 0 <= time.monotonic() - self.checked_at <= 60)

    def record(self):
        return {'model': self.model, 'digest': self.digest, 'service_version': self.service_version,
                'evidence': 'selected-model-capabilities',
                'budget_verified': (self.model, self.digest, self.service_version) ==
                                   (TASK_MODEL, TASK_DIGEST, TASK_SERVICE_VERSION),
                'tool_use_admitted': True, 'think': self.think}


def validate_discovery(base_url, version, tags, show, *, model=TASK_MODEL):
    base_url = local_service_url(base_url)
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
    if context_limits and min(context_limits) < dict(TASK_OPTIONS)['num_ctx']:
        raise ValueError(f'所选模型 {model} 的上下文不足以运行当前工具配置。')
    think = False if parse_thinking(show).supports(False) else None
    return TaskAdmission(base_url, time.monotonic(), model=model, digest=matches[0]['digest'],
                         service_version=service_version, vision_supported='vision' in capabilities, think=think)


@dataclass(frozen=True)
class TaskAuthorization:
    execution: ExecutionSnapshot | None = None
    search: SearchSettings | None = None

    def __post_init__(self):
        if self.execution is None and self.search is None:
            raise ValueError('请至少选择一个工具。')
        if self.execution is not None and not isinstance(self.execution, ExecutionSnapshot):
            raise ValueError('工作区配置无效。')
        if self.search is not None and not isinstance(self.search, SearchSettings):
            raise ValueError('搜索配置无效。')

    def worker_kwargs(self, admission, base_url, *, agent_id, origin, model=None):
        from ai_desktop import config
        if not config.GENERAL_ASSISTANT_TOOLS_ENABLED:
            raise ValueError('设置中已关闭通用助手工具，请在工具执行页重新启用。')
        if agent_id != 'general_assistant' or origin != 'chat':
            raise ValueError('此 Agent 或快捷动作不能使用工具。')
        if not isinstance(admission, TaskAdmission) or not admission.valid(base_url, model):
            raise ValueError('模型准入检查已失效，请重新检查后发送。')
        if self.execution is not None and not self.execution.valid():
            raise ValueError('工具工作区已变化，请重新选择工作目录。')
        return {'model': admission.model, 'think': admission.think,
                'think_setting': ThinkSetting(ThinkMode.OFF if admission.think is False else ThinkMode.MODEL_DEFAULT),
                'think_source': '工具任务配置', 'options': dict(TASK_OPTIONS), 'exact_options': True,
                'tools_admitted': True,
                'execution': self.execution, 'search_settings': self.search}

    def record(self):
        return {'execution': self.execution.record() if self.execution else None,
                'search': self.search.record() if self.search else None}
