"""Search credentials live in macOS Keychain, never in application settings."""
import sys

PROVIDERS = frozenset({"parallel", "exa"})
SERVICE = "com.milin.ai-desktop-assistant.web-search"


def probe_search_runtime() -> dict:
    """Resolve the frozen native bridge without reading or changing a credential."""
    try:
        if getattr(sys, 'frozen', False):
            from ai_desktop.services.credential_broker import BrokerError, request
            try:
                request('probe', 'parallel')
            except BrokerError:
                return {"keychain_available": False, "providers": ["parallel", "exa"]}
            return {"keychain_available": True, "providers": ["parallel", "exa"], "stable_broker": True}
        security = SearchCredentials()._security()
        for name in ("SecItemCopyMatching", "SecItemAdd", "SecItemUpdate", "SecItemDelete"):
            if not callable(getattr(security, name)):
                raise CredentialError("钥匙串组件不可用。")
    except (CredentialError, AttributeError):
        return {"keychain_available": False, "providers": ["parallel", "exa"]}
    return {"keychain_available": True, "providers": ["parallel", "exa"]}


class CredentialError(Exception):
    """Safe user-facing error; native error details may contain secret data."""


class SearchCredentials:
    def __init__(self, backend=None):
        self._backend = backend
        self._direct_backend = backend is not None

    def _broker(self, operation, provider, **kwargs):
        from ai_desktop.services.credential_broker import BrokerError, request
        try:
            return request(operation, provider, **kwargs)
        except BrokerError as exc:
            raise CredentialError(str(exc)) from None

    def connect(self, provider, *, cancelled=None) -> bool:
        """Explicit native authorization only; no HTTP, model calls or secret output."""
        if not self.get(provider, interactive=True, cancelled=cancelled):
            return False
        # "Allow once" is insufficient: every operation gets a new helper
        # process. Only show connected after a separate no-prompt read works.
        if getattr(sys, 'frozen', False) and not self._direct_backend:
            return bool(self.get(provider, interactive=False, cancelled=cancelled))
        return True

    def _security(self):
        if self._backend is None:
            if sys.platform != "darwin":
                raise CredentialError("当前平台不支持 macOS 钥匙串。")
            try:
                import Security
                self._backend = Security
            except ImportError:
                raise CredentialError("钥匙串组件不可用，请检查应用安装。") from None
        return self._backend

    def _query(self, provider):
        if provider not in PROVIDERS:
            raise CredentialError("不支持的搜索服务。")
        security = self._security()
        return {
            security.kSecClass: security.kSecClassGenericPassword,
            security.kSecAttrService: SERVICE,
            security.kSecAttrAccount: provider,
        }

    @staticmethod
    def _check(status, security):
        if status != security.errSecSuccess:
            raise CredentialError("无法访问钥匙串，请解锁登录钥匙串并允许此应用访问。")

    def get(self, provider: str, *, interactive: bool = True, cancelled=None, deadline=None) -> str:
        if getattr(sys, 'frozen', False) and not self._direct_backend:
            return self._broker('get', provider, interactive=interactive, cancelled=cancelled, deadline=deadline)
        if not interactive and not self._direct_backend:
            from ai_desktop.services.credential_reader import CredentialReadError, read_background
            try:
                return read_background(provider, cancelled=cancelled, deadline=deadline)
            except CredentialReadError as exc:
                raise CredentialError(str(exc)) from None
        query = self._query(provider)
        security = self._security()
        query.update({security.kSecReturnData: True, security.kSecMatchLimit: security.kSecMatchLimitOne})
        if not interactive:
            # A background run must never block on a native Keychain prompt.
            query[security.kSecUseAuthenticationUI] = security.kSecUseAuthenticationUIFail
        try:
            status, value = security.SecItemCopyMatching(query, None)
            if status == security.errSecItemNotFound:
                return ""
            self._check(status, security)
            return bytes(value).decode("utf-8")
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("读取搜索密钥失败，请重新保存密钥。") from None

    def set(self, provider: str, value: str) -> None:
        if not value or len(value) > 4096 or any(c.isspace() for c in value):
            raise CredentialError("密钥不能为空、包含空白字符或超过 4096 个字符。")
        if getattr(sys, 'frozen', False) and not self._direct_backend:
            self._broker('set', provider, key=value, interactive=True)
            return
        query = self._query(provider)
        security = self._security()
        try:
            attributes = {security.kSecValueData: value.encode("utf-8")}
            status = security.SecItemUpdate(query, attributes)
            if status == security.errSecItemNotFound:
                status, _ = security.SecItemAdd({**query, **attributes}, None)
            self._check(status, security)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("保存搜索密钥失败，请检查钥匙串权限。") from None

    def delete(self, provider: str) -> None:
        if getattr(sys, 'frozen', False) and not self._direct_backend:
            self._broker('delete', provider, interactive=True)
            return
        query = self._query(provider)
        security = self._security()
        try:
            status = security.SecItemDelete(query)
            if status != security.errSecItemNotFound:
                self._check(status, security)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("移除搜索密钥失败，请检查钥匙串权限。") from None
