"""Immutable execution configuration and workspace-specific preferences."""
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class BashPolicy(str, Enum):
    READONLY_AUTO = 'readonly_auto'
    CONFIRM_ALL = 'confirm_all'


DEFAULT_PATH = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin')


@dataclass(frozen=True)
class ExecutionSnapshot:
    workspace: str
    policy: BashPolicy
    device: int
    inode: int
    search_path: tuple[str, ...] = DEFAULT_PATH
    home: str = ''
    temp_dir: str = ''

    def __post_init__(self):
        if (not isinstance(self.workspace, str) or not os.path.isabs(self.workspace) or
                not isinstance(self.policy, BashPolicy) or not isinstance(self.search_path, tuple) or
                type(self.device) is not int or type(self.inode) is not int):
            raise ValueError('Invalid execution snapshot')
        if not self.search_path or any(not isinstance(item, str) or not os.path.isabs(item) or ':' in item or
                                       any(ord(c) < 32 for c in item) for item in self.search_path):
            raise ValueError('Invalid execution PATH')

    @classmethod
    def create(cls, workspace, policy=BashPolicy.READONLY_AUTO, *, search_path=DEFAULT_PATH):
        if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
            raise ValueError('请先选择工具工作区。')
        root = Path(workspace).expanduser().resolve(strict=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('工具工作区必须是现有目录。')
        path = tuple(search_path)
        if not path or any(not isinstance(item, str) or not os.path.isabs(item) or ':' in item or
                           any(ord(c) < 32 for c in item) for item in path):
            raise ValueError('命令 PATH 必须由绝对目录组成。')
        return cls(str(root), BashPolicy(policy), info.st_dev, info.st_ino, path,
                   str(Path.home()), tempfile.gettempdir())

    def valid(self):
        try:
            root = Path(self.workspace)
            info = root.stat()
            return (root.resolve(strict=True) == root and stat.S_ISDIR(info.st_mode) and
                    (info.st_dev, info.st_ino) == (self.device, self.inode))
        except (OSError, RuntimeError):
            return False

    def environment(self):
        # Construct instead of copying os.environ: no API keys, BASH_ENV,
        # shell functions, Python injection variables or inherited proxies.
        return {'PATH': ':'.join(self.search_path), 'HOME': self.home, 'TMPDIR': self.temp_dir,
                'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8', 'TERM': 'dumb'}

    def record(self):
        return {'workspace': self.workspace, 'bash_policy': self.policy.value, 'path': list(self.search_path)}


def workspace_policy_key(workspace):
    canonical = str(Path(workspace).expanduser().resolve(strict=True))
    return 'workspace_bash_policy:' + hashlib.sha256(canonical.encode()).hexdigest()


def load_workspace_policy(workspace):
    from ai_desktop.utils.storage import get_setting
    try:
        return BashPolicy(get_setting(workspace_policy_key(workspace)) or BashPolicy.READONLY_AUTO.value)
    except (ValueError, OSError, RuntimeError):
        # Corrupt persisted policy should tighten, rather than enable execution.
        return BashPolicy.CONFIRM_ALL


def save_workspace_policy(snapshot):
    from ai_desktop.utils.storage import save_setting
    save_setting(workspace_policy_key(snapshot.workspace), snapshot.policy.value)


def load_execution_preferences():
    from ai_desktop.utils.storage import get_setting
    workspace = get_setting('execution_workspace') or ''
    try:
        raw = json.loads(get_setting('execution_path') or 'null')
        path = (raw if isinstance(raw, list) and raw and all(isinstance(item, str) and
                os.path.isabs(item) and ":" not in item for item in raw) else list(DEFAULT_PATH))
    except ValueError:
        path = list(DEFAULT_PATH)
    policy = load_workspace_policy(workspace) if workspace else BashPolicy.READONLY_AUTO
    return {'execution_workspace': workspace, 'bash_policy': policy.value, 'execution_path': ':'.join(path)}


def save_execution_preferences(data):
    from ai_desktop.utils.storage import save_setting
    workspace = data.get('execution_workspace', '').strip()
    if workspace:
        snapshot = ExecutionSnapshot.create(workspace, data.get('bash_policy', BashPolicy.READONLY_AUTO),
                                            search_path=data.get('execution_path', ':'.join(DEFAULT_PATH)).split(':'))
        save_workspace_policy(snapshot)
        workspace, path = snapshot.workspace, snapshot.search_path
    else:
        path = DEFAULT_PATH
    save_setting('execution_workspace', workspace)
    save_setting('execution_path', json.dumps(list(path)))


def current_execution_snapshot():
    data = load_execution_preferences()
    if not data['execution_workspace']:
        return None
    return ExecutionSnapshot.create(data['execution_workspace'], data['bash_policy'],
                                    search_path=data['execution_path'].split(':'))
