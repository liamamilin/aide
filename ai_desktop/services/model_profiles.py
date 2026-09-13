"""Validated task model profiles and deterministic request-option resolution."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from ai_desktop import config
from ai_desktop.utils import storage

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_PROFILE_NAME = 60
MAX_MODEL_NAME = 200


@dataclass(frozen=True)
class ModelProfile:
    id: str
    name: str
    model: str | None = None
    think: bool | None = None
    temperature: float | None = None
    num_predict: int | None = None
    updated_at: float = 0.0


@dataclass(frozen=True)
class ModelOverrides:
    model: str | None = None
    think: bool | None = None
    temperature: float | None = None
    num_predict: int | None = None


@dataclass(frozen=True)
class ResolvedModelConfig:
    model: str
    think: bool
    temperature: float
    num_predict: int
    profile_name: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def options(self) -> dict[str, int | float]:
        return {
            "temperature": self.temperature,
            "num_predict": self.num_predict,
        }

    @property
    def summary(self) -> str:
        source = self.profile_name or "全局设置"
        thinking = "思考开" if self.think else "思考关"
        return (
            f"{source} · {self.model} · {thinking} · "
            f"温度 {self.temperature:g} · 输出 {self.num_predict}"
        )


def validate_profile(profile: ModelProfile) -> ModelProfile:
    profile_id = str(profile.id).strip()
    name = str(profile.name).strip()
    model = str(profile.model).strip() if profile.model else None
    if not _ID.fullmatch(profile_id):
        raise ValueError("配置 ID 只能包含字母、数字、下划线和连字符，长度 1–64。")
    if not name or len(name) > MAX_PROFILE_NAME:
        raise ValueError(f"配置名称长度必须为 1–{MAX_PROFILE_NAME} 个字符。")
    if model and len(model) > MAX_MODEL_NAME:
        raise ValueError(f"模型名称不能超过 {MAX_MODEL_NAME} 个字符。")
    if profile.think is not None and not isinstance(profile.think, bool):
        raise ValueError("思考选项必须是开启、关闭或继承。")
    temperature = profile.temperature
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise ValueError("temperature 必须是数字或继承。")
        temperature = float(temperature)
        if not 0.0 <= temperature <= 2.0:
            raise ValueError("temperature 必须在 0.0–2.0 之间。")
    num_predict = profile.num_predict
    if num_predict is not None:
        if isinstance(num_predict, bool) or not isinstance(num_predict, int):
            raise ValueError("输出上限必须是整数或继承。")
        if not 1 <= num_predict <= 999_999:
            raise ValueError("输出上限必须在 1–999999 之间。")
    return ModelProfile(
        profile_id,
        name,
        model,
        profile.think,
        temperature,
        num_predict,
        float(profile.updated_at or time.time()),
    )


def _from_record(record: dict) -> ModelProfile | None:
    try:
        options = json.loads(record.get("options", "{}"))
        if not isinstance(options, dict):
            return None
        return validate_profile(
            ModelProfile(
                id=record["id"],
                name=record["name"],
                model=record.get("model") or None,
                think=options.get("think"),
                temperature=options.get("temperature"),
                num_predict=options.get("num_predict"),
                updated_at=record.get("updated_at", 0.0),
            )
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


class ModelProfileManager:
    def __init__(self) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        self.reload()

    @property
    def profiles(self) -> list[ModelProfile]:
        return sorted(self._profiles.values(), key=lambda item: (item.name.casefold(), item.id))

    def reload(self) -> None:
        self._profiles = {}
        for record in storage.list_model_profile_records():
            profile = _from_record(record)
            if profile is not None:
                self._profiles[profile.id] = profile

    def save(self, profile: ModelProfile) -> ModelProfile:
        normalized = validate_profile(profile)
        options = {
            "think": normalized.think,
            "temperature": normalized.temperature,
            "num_predict": normalized.num_predict,
        }
        storage.save_model_profile_record(
            {
                "id": normalized.id,
                "name": normalized.name,
                "model": normalized.model or "",
                "options": json.dumps(options, ensure_ascii=False, separators=(",", ":")),
                "updated_at": normalized.updated_at,
            }
        )
        self._profiles[normalized.id] = normalized
        return normalized

    def replace_all(self, profiles: list[ModelProfile]) -> list[ModelProfile]:
        normalized = [validate_profile(profile) for profile in profiles]
        ids = [profile.id for profile in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("模型配置 ID 不能重复。")
        for profile in normalized:
            self.save(profile)
        for profile_id in set(self._profiles) - set(ids):
            storage.delete_model_profile_record(profile_id)
            self._profiles.pop(profile_id, None)
        return self.profiles

    def delete(self, profile_id: str) -> bool:
        deleted = storage.delete_model_profile_record(profile_id)
        self._profiles.pop(profile_id, None)
        return deleted

    def resolve(
        self,
        *,
        global_model: str,
        agent_profile_id: str | None = None,
        action_profile_id: str | None = None,
        temporary: ModelOverrides | None = None,
        available_models: list[str] | None = None,
    ) -> ResolvedModelConfig:
        warnings: list[str] = []
        layers: list[tuple[str, ModelOverrides]] = []
        if temporary is not None:
            layers.append(("本次覆盖", temporary))
        for label, profile_id in (("动作配置", action_profile_id), ("Agent 配置", agent_profile_id)):
            if not profile_id:
                continue
            profile = self._profiles.get(profile_id)
            if profile is None:
                warnings.append(f"{label}已被删除，已继承下一层设置。")
                continue
            layers.append(
                (
                    profile.name,
                    ModelOverrides(
                        profile.model,
                        profile.think,
                        profile.temperature,
                        profile.num_predict,
                    ),
                )
            )

        available = set(available_models or [])
        models_known = available_models is not None
        model = global_model or config.OLLAMA_MODEL
        selected_profile = ""
        for label, layer in layers:
            if layer.model:
                if models_known and layer.model not in available:
                    warnings.append(f"配置“{label}”的模型 {layer.model} 当前不可用，已继承下一层模型。")
                else:
                    model = layer.model
                    selected_profile = selected_profile or label
                    break

        def first(field: str, default):
            nonlocal selected_profile
            for label, layer in layers:
                value = getattr(layer, field)
                if value is not None:
                    selected_profile = selected_profile or label
                    return value
            return default

        return ResolvedModelConfig(
            model=model,
            think=bool(first("think", config.OLLAMA_THINK)),
            temperature=float(first("temperature", config.OLLAMA_TEMPERATURE)),
            num_predict=int(first("num_predict", config.OLLAMA_NUM_PREDICT)),
            profile_name=selected_profile,
            warnings=tuple(warnings),
        )
