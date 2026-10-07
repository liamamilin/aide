"""Saved tool choices; no run, model admission, or command approval is persisted."""
import json
import logging
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ai_desktop.services.execution_context import BashPolicy, ExecutionSnapshot, current_execution_snapshot
from ai_desktop.services.task_admission import TaskAuthorization
from ai_desktop.services.web_search import ENDPOINTS, SearchSettings
from ai_desktop.utils.storage import get_setting, save_setting

logger = logging.getLogger(__name__)
SETTING_KEY = 'default_chat_tools'


@dataclass(frozen=True)
class ToolPreferences:
    execution: ExecutionSnapshot | None = None
    search_provider: str | None = None

    def __post_init__(self):
        if self.execution is not None and not isinstance(self.execution, ExecutionSnapshot):
            raise ValueError('Invalid saved workspace')
        if self.search_provider is not None and self.search_provider not in ENDPOINTS:
            raise ValueError('Invalid saved search provider')
        if self.execution is None and self.search_provider is None:
            raise ValueError('No saved tools')

    @classmethod
    def from_authorization(cls, authorization):
        if authorization is None:
            return None
        return cls(authorization.execution, authorization.search.provider if authorization.search else None)

    def for_agent(self, agent_id):
        search = (replace(SearchSettings.from_config(), provider=self.search_provider)
                  if self.search_provider else None)
        return TaskAuthorization(self.execution, search, agent_id=agent_id)

    def updated(self, changed):
        from ai_desktop import config
        execution = self.execution
        if execution and set(changed) & {'execution_workspace', 'bash_policy', 'execution_path'}:
            execution = current_execution_snapshot()
        provider = self.search_provider
        if provider and 'search_provider' in changed:
            provider = config.SEARCH_PROVIDER
        return ToolPreferences(execution, provider) if execution or provider else None

    @staticmethod
    def save(preferences):
        record = None
        if preferences is not None:
            snapshot = preferences.execution
            execution = ({**snapshot.record(), 'device': snapshot.device, 'inode': snapshot.inode}
                         if snapshot else None)
            record = {'version': 1, 'execution': execution, 'search_provider': preferences.search_provider}
        save_setting(SETTING_KEY, json.dumps(record, ensure_ascii=False))

    @classmethod
    def load(cls):
        raw = get_setting(SETTING_KEY)
        if not raw:
            return None
        try:
            record = json.loads(raw)
            if record is None:
                return None
            if (not isinstance(record, dict) or type(record.get('version')) is not int
                    or record['version'] != 1):
                raise ValueError('Unknown saved tool settings')
            execution = record['execution']
            if execution is not None:
                if not isinstance(execution, dict) or not isinstance(execution['path'], list):
                    raise ValueError('Invalid saved workspace')
                # Keep the approved directory identity across new conversations
                # and restarts. A replaced directory must not become trusted.
                execution = ExecutionSnapshot(
                    execution['workspace'], BashPolicy(execution['bash_policy']),
                    execution['device'], execution['inode'], tuple(execution['path']),
                    str(Path.home()), tempfile.gettempdir())
            return cls(execution, record['search_provider'])
        except (ValueError, KeyError, TypeError):
            logger.warning('Saved tool settings are invalid; tools remain off')
            return None
