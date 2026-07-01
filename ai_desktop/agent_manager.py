"""
Agent 管理器 —— 管理 Agent 列表、切换、保存

职责：
- 合并内置 + 自定义 Agent 列表
- 切换活跃 Agent（更新 SQLite、返回 Agent）
- 保存自定义 Agent 到 SQLite
- 提供当前活跃 Agent 和完整列表的访问
"""
import logging

from ai_desktop.config import AGENTS, DEFAULT_AGENT_INDEX, Agent
from ai_desktop.ui.agent_editor import AgentDef
from ai_desktop.utils.storage import get_setting, load_custom_agents, save_custom_agents, save_setting

logger = logging.getLogger(__name__)


def _normalize_custom_agent(data: dict) -> dict | None:
    required = ("id", "name", "icon", "system_prompt")
    if not isinstance(data, dict):
        return None
    normalized = {key: str(data.get(key, "")).strip() for key in required}
    if not normalized["id"] or not normalized["name"] or not normalized["system_prompt"]:
        return None
    if not normalized["icon"]:
        normalized["icon"] = "🤖"
    return normalized


def _normalize_custom_agents(data: list[dict]) -> list[dict]:
    result = []
    seen = {ag.id for ag in AGENTS}
    for item in data:
        normalized = _normalize_custom_agent(item)
        if normalized is None:
            logger.warning("Skipping invalid custom agent: %s", item)
            continue
        if normalized["id"] in seen:
            logger.warning("Skipping duplicate custom agent id: %s", normalized["id"])
            continue
        seen.add(normalized["id"])
        result.append(normalized)
    return result


class AgentManager:
    """管理 Agent 列表和切换"""

    def __init__(self) -> None:
        # 合并内置 + 自定义 Agent
        self._all_agents: list[Agent] = list(AGENTS)
        self._custom_agents: list[AgentDef] = []
        for d in _normalize_custom_agents(load_custom_agents()):
            ag = Agent(id=d["id"], name=d["name"], icon=d["icon"], system_prompt=d["system_prompt"])
            a_def = AgentDef(id=d["id"], name=d["name"], icon=d["icon"],
                            system_prompt=d["system_prompt"], builtin=False)
            self._all_agents.append(ag)
            self._custom_agents.append(a_def)

        # 恢复上次使用的 Agent
        self._active_agent: Agent = self._all_agents[DEFAULT_AGENT_INDEX]
        saved_agent_id = get_setting("last_agent_id")
        if saved_agent_id:
            for ag in self._all_agents:
                if ag.id == saved_agent_id:
                    self._active_agent = ag
                    break

    @property
    def builtin_agents(self) -> list[Agent]:
        """返回内置 Agent 列表"""
        return list(AGENTS)

    @property
    def all_agents(self) -> list[Agent]:
        """返回所有 Agent（内置 + 自定义）"""
        return list(self._all_agents)

    @property
    def active_agent(self) -> Agent:
        """返回当前活跃 Agent"""
        return self._active_agent

    @property
    def custom_agents(self) -> list[AgentDef]:
        """返回自定义 Agent 列表"""
        return list(self._custom_agents)

    def switch(self, agent: Agent) -> Agent:
        """切换活跃 Agent

        Args:
            agent: 目标 Agent

        Returns:
            实际切换到的 Agent（如果目标不在列表中，回退到默认）
        """
        # 查找 Agent（用 id 匹配，因为传入的可能是不同实例）
        found = None
        for ag in self._all_agents:
            if ag.id == agent.id:
                found = ag
                break

        if found is None:
            logger.warning("Agent %s not found, falling back to default", agent.id)
            found = self._all_agents[DEFAULT_AGENT_INDEX]

        self._active_agent = found
        save_setting("last_agent_id", found.id)
        logger.info("Agent switched: %s", found.name)
        return found

    def save_custom(self, data: list[dict]) -> None:
        """保存自定义 Agent 列表

        Args:
            data: 自定义 Agent 数据列表，每项包含 id, name, icon, system_prompt
        """
        data = _normalize_custom_agents(data)
        save_custom_agents(data)

        # 重建合并列表
        self._all_agents = list(AGENTS)
        self._custom_agents.clear()
        for d in data:
            ag = Agent(id=d["id"], name=d["name"], icon=d["icon"], system_prompt=d["system_prompt"])
            self._all_agents.append(ag)
            self._custom_agents.append(
                AgentDef(id=d["id"], name=d["name"], icon=d["icon"],
                         system_prompt=d["system_prompt"], builtin=False)
            )

        # 确保当前 Agent 仍在列表中
        if self._active_agent not in self._all_agents:
            self._active_agent = self._all_agents[DEFAULT_AGENT_INDEX]

        logger.info("Custom agents saved (%d custom)", len(data))
