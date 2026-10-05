"""
设置管理器 —— 从 SQLite 加载持久化配置，应用设置变更

职责：
- 从 SQLite 加载持久化配置到 config 模块
- 接收设置面板的 dict，逐项比较并写入 SQLite + 更新 config 模块变量
- 返回变更的 key 列表，供调用方决定是否需要额外操作（如热键重注册）
"""
import json
import logging

from ai_desktop import config
from ai_desktop.capture.hotkey_listener import validate_hotkey
from ai_desktop.llm.thinking import ThinkSetting, load_think_setting, normalize_think
from ai_desktop.services.execution_context import (
    ExecutionSnapshot,
    load_execution_preferences,
    save_execution_preferences,
)
from ai_desktop.services.web_search import ENDPOINTS, MAX_SEARCH_RESULTS, PARALLEL_MODES
from ai_desktop.utils.storage import get_setting, save_setting

logger = logging.getLogger(__name__)

# 设置项映射: (dict_key, config_attr, type_converter)
_SETTING_MAP = [
    ("base_url",        "OLLAMA_BASE_URL",       str),
    ("timeout",         "OLLAMA_TIMEOUT",        int),
    ("num_ctx",         "OLLAMA_NUM_CTX",        int),
    ("num_predict",     "OLLAMA_NUM_PREDICT",    int),
    ("temperature",     "OLLAMA_TEMPERATURE",    float),
    ("top_p",           "OLLAMA_TOP_P",          float),
    ("top_k",           "OLLAMA_TOP_K",          int),
    ("repeat_penalty",  "OLLAMA_REPEAT_PENALTY", float),
    ("max_rounds",      "OLLAMA_MAX_ROUNDS",     int),
    ("hotkey",          "HOTKEY",                str),
    ("think",           "OLLAMA_THINK",          ThinkSetting),
    ("quick_actions",   "QUICK_ACTIONS_ENABLED", bool),
    ("desktop_pet",     "DESKTOP_PET_ENABLED",   bool),
    ("pet_reduce_motion", "DESKTOP_PET_REDUCE_MOTION", bool),
    ("pet_size",        "DESKTOP_PET_SIZE",      str),
    ("pet_source",      "PET_SOURCE",            str),
    ("pet_name",        "PET_NAME",              str),
    ("search_provider", "SEARCH_PROVIDER",       str),
    ("search_max_results", "SEARCH_MAX_RESULTS", int),
    ("search_timeout", "SEARCH_TIMEOUT",         int),
    ("search_parallel_mode", "SEARCH_PARALLEL_MODE", str),
    ("task_tools_enabled", "GENERAL_ASSISTANT_TOOLS_ENABLED", bool),
    ("task_max_model_rounds", "TASK_MAX_MODEL_ROUNDS", int),
    ("task_max_tool_calls", "TASK_MAX_TOOL_CALLS", int),
    ("task_max_search_calls", "TASK_MAX_SEARCH_CALLS", int),
]

_DB_KEY_MAP = {
    "base_url":       "ollama_base_url",
    "timeout":        "ollama_timeout",
    "num_ctx":        "ollama_num_ctx",
    "num_predict":    "ollama_num_predict",
    "temperature":    "ollama_temperature",
    "top_p":          "ollama_top_p",
    "top_k":          "ollama_top_k",
    "repeat_penalty": "ollama_repeat_penalty",
    "max_rounds":     "ollama_max_rounds",
    "hotkey":         "hotkey",
    "think":          "ollama_think",
    "quick_actions":  "quick_actions_enabled",
    "desktop_pet":    "desktop_pet_enabled",
    "pet_reduce_motion": "desktop_pet_reduce_motion",
    "pet_size":       "desktop_pet_size",
    "pet_source":     "pet_source",
    "pet_name":       "pet_name",
    "search_provider": "search_provider",
    "search_max_results": "search_max_results",
    "search_timeout": "search_timeout",
    "search_parallel_mode": "search_parallel_mode",
    "task_tools_enabled": "general_assistant_tools_enabled",
    "task_max_model_rounds": "task_max_model_rounds",
    "task_max_tool_calls": "task_max_tool_calls",
    "task_max_search_calls": "task_max_search_calls",
}

_PET_SIZES = frozenset({"small", "medium", "large"})
_PET_SOURCES = frozenset({"built-in", "petdex"})


def _validate_search(key, value):
    if key == "search_provider" and value not in ENDPOINTS:
        raise ValueError("invalid search provider")
    if key == "search_parallel_mode" and value not in PARALLEL_MODES:
        raise ValueError("invalid search mode")
    if key == "search_max_results" and not 1 <= value <= MAX_SEARCH_RESULTS:
        raise ValueError("invalid source count")
    if key in {"task_max_model_rounds", "task_max_tool_calls", "task_max_search_calls"} and not 1 <= value <= 99:
        raise ValueError("invalid task count")
    if key == "search_timeout" and not 5 <= value <= 120:
        raise ValueError("invalid search timeout")


class SettingsManager:
    """管理持久化设置的加载和应用"""

    def current(self) -> dict:
        """Return non-secret settings only, shared by the dialog and callers."""
        return {**{key: getattr(config, attr) for key, attr, _ in _SETTING_MAP},
                **load_execution_preferences()}

    def load(self) -> None:
        """从 SQLite 加载持久化配置，覆盖 config.py 默认值"""
        for dict_key, attr, conv in _SETTING_MAP:
            db_key = _DB_KEY_MAP[dict_key]
            val = get_setting(db_key)
            if val:
                try:
                    if dict_key == "think":
                        setattr(config, attr, load_think_setting(val))
                    elif conv is bool:
                        setattr(config, attr, val.lower() == "true")
                    else:
                        converted = conv(val)
                        _validate_search(dict_key, converted)
                        if dict_key == "pet_size" and converted not in _PET_SIZES:
                            raise ValueError("invalid pet size")
                        if dict_key == "pet_source" and converted not in _PET_SOURCES:
                            raise ValueError("invalid pet source")
                        setattr(config, attr, converted)
                except (ValueError, TypeError):
                    if dict_key == "think":
                        config.OLLAMA_THINK = ThinkSetting()
                    logger.warning("Invalid setting %s=%s, keeping default", db_key, val)

    def apply(self, data: dict) -> list[str]:
        """应用设置变更，返回实际变更的 key 列表

        Args:
            data: 设置面板传来的 dict，key 为 dict_key（如 "base_url"）

        Returns:
            实际变更的 key 列表（如 ["base_url", "hotkey"]）
        """
        changed: list[str] = []
        execution_keys = {'execution_workspace', 'bash_policy', 'execution_path'}
        if execution_keys & data.keys():
            current_execution = load_execution_preferences()
            execution = {**current_execution, **{key: data[key] for key in execution_keys if key in data}}
            try:
                workspace = execution['execution_workspace'].strip()
                if workspace:
                    snapshot = ExecutionSnapshot.create(workspace, execution['bash_policy'],
                                                        search_path=execution['execution_path'].split(':'))
                    execution['execution_workspace'] = snapshot.workspace
                if execution != current_execution:
                    save_execution_preferences(execution)
                    changed.extend(key for key in sorted(execution_keys) if execution[key] != current_execution[key])
            except (ValueError, OSError, TypeError, AttributeError, RuntimeError):
                logger.warning("Invalid execution preferences; keeping saved workspace and policy")
        for dict_key, attr, conv in _SETTING_MAP:
            if dict_key not in data:
                continue
            new_value = data[dict_key]
            if dict_key == "hotkey" and not validate_hotkey(str(new_value)):
                logger.warning("Invalid value for %s: %s", dict_key, new_value)
                continue
            current = getattr(config, attr)
            # 类型转换后比较
            try:
                if dict_key == 'task_tools_enabled' and type(new_value) is not bool:
                    raise ValueError('invalid tool permission')
                if dict_key in {"search_timeout", "search_max_results", "task_max_model_rounds",
                                "task_max_tool_calls", "task_max_search_calls"} and type(new_value) not in {int, str}:
                    raise ValueError("invalid search integer")
                converted = (normalize_think(new_value) if dict_key == "think" else
                             conv(new_value) if not isinstance(new_value, conv) else new_value)
                _validate_search(dict_key, converted)
                if dict_key == "pet_size":
                    converted = converted.strip().lower()
                    if converted not in _PET_SIZES:
                        raise ValueError("invalid pet size")
                if dict_key == "pet_source":
                    converted = converted.strip().lower()
                    if converted not in _PET_SOURCES:
                        raise ValueError("invalid pet source")
            except (ValueError, TypeError):
                logger.warning("Invalid value for %s: %s", dict_key, new_value)
                continue
            if converted != current:
                try:
                    setattr(config, attr, converted)
                    db_key = _DB_KEY_MAP[dict_key]
                    save_setting(db_key, json.dumps(converted.record(), ensure_ascii=False)
                                 if dict_key == "think" else str(converted))
                    changed.append(dict_key)
                except (ValueError, TypeError):
                    logger.warning("Failed to apply %s=%s", dict_key, new_value)
        return changed
