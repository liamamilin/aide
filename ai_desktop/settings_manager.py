"""
设置管理器 —— 从 SQLite 加载持久化配置，应用设置变更

职责：
- 从 SQLite 加载持久化配置到 config 模块
- 接收设置面板的 dict，逐项比较并写入 SQLite + 更新 config 模块变量
- 返回变更的 key 列表，供调用方决定是否需要额外操作（如热键重注册）
"""
import logging

from ai_desktop import config
from ai_desktop.capture.hotkey_listener import validate_hotkey
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
    ("think",           "OLLAMA_THINK",          bool),
    ("tts_voice",       "TTS_VOICE",             str),
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
    "tts_voice":      "tts_voice",
}


class SettingsManager:
    """管理持久化设置的加载和应用"""

    def load(self) -> None:
        """从 SQLite 加载持久化配置，覆盖 config.py 默认值"""
        for dict_key, attr, conv in _SETTING_MAP:
            db_key = _DB_KEY_MAP[dict_key]
            val = get_setting(db_key)
            if val:
                try:
                    if conv is bool:
                        setattr(config, attr, val.lower() == "true")
                    else:
                        setattr(config, attr, conv(val))
                except (ValueError, TypeError):
                    logger.warning("Invalid setting %s=%s, keeping default", db_key, val)

    def apply(self, data: dict) -> list[str]:
        """应用设置变更，返回实际变更的 key 列表

        Args:
            data: 设置面板传来的 dict，key 为 dict_key（如 "base_url"）

        Returns:
            实际变更的 key 列表（如 ["base_url", "hotkey"]）
        """
        changed: list[str] = []
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
                converted = conv(new_value) if not isinstance(new_value, conv) else new_value
            except (ValueError, TypeError):
                logger.warning("Invalid value for %s: %s", dict_key, new_value)
                continue
            if converted != current:
                try:
                    setattr(config, attr, converted)
                    db_key = _DB_KEY_MAP[dict_key]
                    save_setting(db_key, str(converted))
                    changed.append(dict_key)
                except (ValueError, TypeError):
                    logger.warning("Failed to apply %s=%s", dict_key, new_value)
        return changed
