"""Bound native Keychain reads in a disposable process; secrets stay in pipes."""
import json
import os
import subprocess
import sys
import time

READ_TIMEOUT = 6.0


class CredentialReadError(Exception):
    pass


def reader_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--search-credential-read']
    return [sys.executable, '-m', 'ai_desktop.services.credential_reader']


def read_background(provider, *, cancelled=None, deadline=None, timeout=READ_TIMEOUT):
    from ai_desktop.services.search_credentials import PROVIDERS
    if provider not in PROVIDERS:
        raise CredentialReadError('不支持的搜索服务。')
    expires = time.monotonic() + timeout
    if deadline is not None:
        expires = min(expires, deadline)
    def interrupted():
        return (cancelled is not None and cancelled.is_set()) or time.monotonic() >= expires
    if interrupted():
        raise CredentialReadError('读取搜索密钥已停止或超时。')
    process = None
    try:
        process = subprocess.Popen(reader_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        request = json.dumps({'provider': provider, 'parent': os.getpid()}).encode()
        while not interrupted():
            try:
                output, _ = process.communicate(input=request, timeout=min(.05, expires-time.monotonic()))
                break
            except subprocess.TimeoutExpired:
                request = None
        else:
            raise CredentialReadError('读取搜索密钥已停止或超时，请在设置中检查钥匙串访问。')
        if process.returncode != 0 or len(output) > 8192:
            raise CredentialReadError('无法读取搜索密钥，请在设置中重新保存并允许钥匙串访问。')
        value = json.loads(output)
        key = value.get('key') if isinstance(value, dict) else None
        if not isinstance(key, str) or len(key) > 4096 or any(c.isspace() for c in key):
            raise CredentialReadError('无法读取搜索密钥，请在设置中检查钥匙串访问。')
        return key
    except CredentialReadError:
        raise
    except Exception:
        # Never include native errors, subprocess output or key values.
        raise CredentialReadError('读取搜索密钥失败，请在设置中检查钥匙串访问。') from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()


def child_main():
    # Frozen builds use the signed native broker directly. Do not leave an App
    # command-line relay that could export a real key to an untrusted caller.
    if getattr(sys, 'frozen', False):
        return 2
    # Internal pipe-only IPC: never write a key to an interactive terminal.
    if sys.stdin.isatty() or sys.stdout.isatty():
        return 2
    try:
        request = json.loads(sys.stdin.buffer.read(1024))
        if not isinstance(request, dict) or request.get('parent') != os.getppid():
            return 2
        from ai_desktop.services.search_credentials import PROVIDERS, SearchCredentials
        provider = request.get('provider')
        if not isinstance(provider, str) or provider not in PROVIDERS:
            return 2
        backend = SearchCredentials()._security()
        # SecItem's UI flag alone does not suppress legacy file-Keychain ACL
        # dialogs. Restrict interaction only in this disposable process, never
        # in the application where settings may explicitly request access.
        if backend.SecKeychainSetUserInteractionAllowed(False) != backend.errSecSuccess:
            return 1
        key = SearchCredentials(backend).get(provider, interactive=False)
        sys.stdout.buffer.write(json.dumps({'key': key}).encode())
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(child_main())
