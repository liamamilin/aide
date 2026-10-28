"""Agent definition shared by the Qt editor and the headless core."""
from dataclasses import dataclass


@dataclass
class AgentDef:
    id: str
    name: str
    icon: str
    system_prompt: str
    builtin: bool = False
    profile_id: str | None = None
