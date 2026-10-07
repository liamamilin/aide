"""Private, cancellable IPC to the stable signed native Keychain identity."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKGROUND_TIMEOUT = 6.0
INTERACTIVE_TIMEOUT = 120.0


class BrokerError(Exception):
    pass


def broker_command():
    directory = Path(sys.executable).parent.parent / 'Helpers'
    helper = directory / 'AIDESearchCredentials'
    try:
        manifest = json.loads((directory.parent / 'Resources/credentials.json').read_text())
        if not helper.is_file() or hashlib.sha256(helper.read_bytes()).hexdigest() != manifest['sha256']:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise BrokerError('钥匙串助手缺失或损坏，请重新构建应用。') from None
    return [str(helper)]


def request(operation, provider, *, key=None, interactive=False, cancelled=None, deadline=None,
            fixture=None):
    if operation not in {'get', 'set', 'delete', 'probe'} or provider not in {'parallel', 'exa'}:
        raise BrokerError('不支持的密钥操作。')
    if key is not None and (not isinstance(key, str) or not key or len(key) > 4096 or
                            any(c.isspace() for c in key)):
        raise BrokerError('密钥不能为空、包含空白字符或超过 4096 个字符。')
    if fixture is not None and (not isinstance(fixture, str) or len(fixture) != 32 or
                               any(c not in '0123456789abcdef' for c in fixture)):
        raise BrokerError('测试条目无效。')
    expires = time.monotonic() + (INTERACTIVE_TIMEOUT if interactive else BACKGROUND_TIMEOUT)
    if deadline is not None:
        expires = min(expires, deadline)

    def interrupted():
        return (cancelled is not None and cancelled.is_set()) or time.monotonic() >= expires

    if interrupted():
        raise BrokerError('钥匙串操作已停止或超时。')
    payload = {'operation': operation, 'provider': provider, 'parent': os.getpid(),
               'interactive': interactive}
    if key is not None:
        payload['key'] = key
    if fixture is not None:
        payload['fixture'] = fixture
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > 8192:
        raise BrokerError('密钥请求过大。')
    process = None
    try:
        process = subprocess.Popen(broker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        while not interrupted():
            try:
                output, _ = process.communicate(input=encoded, timeout=max(.001, min(.05, expires-time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                encoded = None
        else:
            raise BrokerError('钥匙串操作已停止或超时。')
        if process.returncode != 0 or len(output) > 8192:
            raise BrokerError('无法访问搜索密钥。请在设置 → 联网搜索中连接钥匙串，并允许密钥助手访问。')
        reply = json.loads(output)
        value = reply.get('key') if isinstance(reply, dict) else None
        if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() for c in value):
            raise BrokerError('钥匙串助手响应无效，请检查应用安装。')
        return value
    except BrokerError:
        raise
    except Exception:
        raise BrokerError('钥匙串操作失败，请检查应用安装与钥匙串访问。') from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
