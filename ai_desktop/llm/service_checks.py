"""Asynchronous Ollama and release checks owned by the Qt UI thread."""
import json
import logging
from dataclasses import dataclass
from enum import Enum

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ai_desktop import config
from ai_desktop.utils.update_checker import (
    mark_update_check_started,
    parse_update_info,
    update_check_due,
)

logger = logging.getLogger(__name__)


class ServiceState(str, Enum):
    CHECKING = "checking"
    ONLINE = "online"
    EMPTY = "empty"
    OFFLINE = "offline"
    INVALID = "invalid"


class ImageCapability(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServiceCheckResult:
    sequence: int
    base_url: str
    state: ServiceState
    models: tuple[str, ...] = ()
    model_versions: tuple[tuple[str, str], ...] = ()
    error: str = ""


@dataclass(frozen=True)
class ModelCapabilityResult:
    sequence: int
    base_url: str
    model: str
    version: str
    capability: ImageCapability
    error: str = ""


def normalize_service_url(base_url: str) -> str:
    """Return one stable cache/request key for equivalent service URLs."""
    return str(base_url).strip().rstrip("/")


def model_cache_key(base_url: str) -> str:
    return f"cached_models:{normalize_service_url(base_url)}"


def model_versions_cache_key(base_url: str) -> str:
    return f"cached_model_versions:{normalize_service_url(base_url)}"


def model_capability_cache_key(base_url: str, model: str, version: str) -> str:
    identity = json.dumps(
        [normalize_service_url(base_url), model, version],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"cached_image_capability:{identity}"


class AsyncServiceChecks(QObject):
    """Run short JSON checks without blocking the GUI event loop.

    There is at most one model-list request, one capability request, and one
    update request in flight. Repeated checks for the same identity are reused;
    a changed address or model aborts the stale request first.
    """

    service_checked = pyqtSignal(object)
    model_capability_checked = pyqtSignal(object)
    update_checked = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None, *, service_timeout_ms: int = 5000,
                 update_timeout_ms: int = 10000) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._service_timeout_ms = service_timeout_ms
        self._update_timeout_ms = update_timeout_ms
        self._service_sequence = 0
        self._capability_sequence = 0
        self._service_active: dict | None = None
        self._capability_active: dict | None = None
        self._update_active: dict | None = None

    @property
    def service_request_count(self) -> int:
        """Number of actual Ollama HTTP requests started (useful for diagnostics)."""
        return self._service_sequence

    def check_service(self, base_url: str) -> int:
        base_url = normalize_service_url(base_url)
        active = self._service_active
        if active is not None and active["base_url"] == base_url:
            return active["sequence"]

        self.cancel_service()
        self._service_sequence += 1
        sequence = self._service_sequence
        request = QNetworkRequest(QUrl(f"{base_url}/api/tags"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.ManualRedirectPolicy,
        )
        reply = self._manager.get(request)
        timer = QTimer(self)
        timer.setSingleShot(True)
        active = {
            "sequence": sequence,
            "base_url": base_url,
            "reply": reply,
            "timer": timer,
        }
        self._service_active = active
        reply.finished.connect(lambda active=active: self._finish_service(active))
        timer.timeout.connect(lambda active=active: self._timeout_service(active))
        timer.start(max(1, self._service_timeout_ms))
        return sequence

    def cancel_service(self) -> None:
        active = self._service_active
        if active is None:
            return
        self._service_active = None
        active["timer"].stop()
        active["timer"].deleteLater()
        reply = active["reply"]
        if reply.isRunning():
            reply.abort()
        reply.deleteLater()

    def _timeout_service(self, active: dict) -> None:
        if self._service_active is not active:
            return
        result = ServiceCheckResult(
            active["sequence"], active["base_url"], ServiceState.OFFLINE,
            error="连接超时",
        )
        self._release_service(active, abort=True)
        self.service_checked.emit(result)

    def _finish_service(self, active: dict) -> None:
        if self._service_active is not active:
            return
        reply = active["reply"]
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NoError or status != 200:
            detail = f"HTTP {status}" if status is not None else reply.errorString()
            result = ServiceCheckResult(
                active["sequence"], active["base_url"], ServiceState.OFFLINE,
                error=detail,
            )
        else:
            result = self._parse_service_response(
                active["sequence"], active["base_url"], bytes(reply.readAll()),
            )
        self._release_service(active)
        self.service_checked.emit(result)

    @staticmethod
    def _parse_service_response(sequence: int, base_url: str, body: bytes) -> ServiceCheckResult:
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                raise TypeError("models must be a list")
            raw_models = data["models"]
            names = []
            versions = []
            for item in raw_models:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise TypeError("model entry has no name")
                name = item["name"].strip()
                if not name:
                    raise TypeError("model name is empty")
                if name not in names:
                    names.append(name)
                    digest = item.get("digest", "")
                    versions.append((name, digest.strip() if isinstance(digest, str) else ""))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            return ServiceCheckResult(
                sequence, base_url, ServiceState.INVALID, error=str(exc),
            )
        state = ServiceState.ONLINE if names else ServiceState.EMPTY
        return ServiceCheckResult(
            sequence,
            base_url,
            state,
            tuple(names),
            tuple(versions),
        )

    def _release_service(self, active: dict, *, abort: bool = False) -> None:
        if self._service_active is not active:
            return
        self._service_active = None
        active["timer"].stop()
        active["timer"].deleteLater()
        reply = active["reply"]
        if abort and reply.isRunning():
            reply.abort()
        reply.deleteLater()

    def check_model_capability(
        self, base_url: str, model: str, version: str = "",
    ) -> int:
        """Read one model's declared image capability through /api/show."""
        base_url = normalize_service_url(base_url)
        identity = (base_url, model, version)
        active = self._capability_active
        if active is not None and active["identity"] == identity:
            return active["sequence"]

        self.cancel_model_capability()
        self._capability_sequence += 1
        sequence = self._capability_sequence
        request = QNetworkRequest(QUrl(f"{base_url}/api/show"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.ManualRedirectPolicy,
        )
        body = json.dumps({"model": model}, ensure_ascii=False).encode("utf-8")
        reply = self._manager.post(request, body)
        timer = QTimer(self)
        timer.setSingleShot(True)
        active = {
            "sequence": sequence,
            "base_url": base_url,
            "model": model,
            "version": version,
            "identity": identity,
            "reply": reply,
            "timer": timer,
        }
        self._capability_active = active
        reply.finished.connect(lambda active=active: self._finish_model_capability(active))
        timer.timeout.connect(lambda active=active: self._timeout_model_capability(active))
        timer.start(max(1, self._service_timeout_ms))
        return sequence

    def cancel_model_capability(self) -> None:
        active = self._capability_active
        if active is not None:
            self._release_model_capability(active, abort=True)

    def _timeout_model_capability(self, active: dict) -> None:
        if self._capability_active is not active:
            return
        result = self._model_capability_result(
            active,
            ImageCapability.UNKNOWN,
            "能力检查超时",
        )
        self._release_model_capability(active, abort=True)
        self.model_capability_checked.emit(result)

    def _finish_model_capability(self, active: dict) -> None:
        if self._capability_active is not active:
            return
        reply = active["reply"]
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NoError or status != 200:
            detail = f"HTTP {status}" if status is not None else reply.errorString()
            result = self._model_capability_result(
                active,
                ImageCapability.UNKNOWN,
                detail,
            )
        else:
            capability, error = self._parse_model_capability(bytes(reply.readAll()))
            result = self._model_capability_result(active, capability, error)
        self._release_model_capability(active)
        self.model_capability_checked.emit(result)

    @staticmethod
    def _parse_model_capability(body: bytes) -> tuple[ImageCapability, str]:
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise TypeError("model details must be an object")
            capabilities = data.get("capabilities")
            if capabilities is None:
                return ImageCapability.UNKNOWN, "服务未返回 capabilities"
            if not isinstance(capabilities, list) or not all(
                isinstance(item, str) for item in capabilities
            ):
                raise TypeError("capabilities must be a string list")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            return ImageCapability.UNKNOWN, str(exc)
        normalized = {item.strip().lower() for item in capabilities}
        capability = (
            ImageCapability.SUPPORTED
            if "vision" in normalized
            else ImageCapability.UNSUPPORTED
        )
        return capability, ""

    @staticmethod
    def _model_capability_result(
        active: dict,
        capability: ImageCapability,
        error: str = "",
    ) -> ModelCapabilityResult:
        return ModelCapabilityResult(
            active["sequence"],
            active["base_url"],
            active["model"],
            active["version"],
            capability,
            error,
        )

    def _release_model_capability(
        self, active: dict, *, abort: bool = False,
    ) -> None:
        if self._capability_active is not active:
            return
        self._capability_active = None
        active["timer"].stop()
        active["timer"].deleteLater()
        reply = active["reply"]
        if abort and reply.isRunning():
            reply.abort()
        reply.deleteLater()

    def check_for_update(self, *, force: bool = False, url: str = "") -> bool:
        """Start one release request; return False when gated or already active."""
        if self._update_active is not None:
            return False
        if not force and not update_check_due():
            return False
        if not force:
            mark_update_check_started()
        endpoint = url or f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", b"ai-desktop-assistant")
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.ManualRedirectPolicy,
        )
        reply = self._manager.get(request)
        timer = QTimer(self)
        timer.setSingleShot(True)
        active = {"reply": reply, "timer": timer}
        self._update_active = active
        reply.finished.connect(lambda active=active: self._finish_update(active))
        timer.timeout.connect(lambda active=active: self._timeout_update(active))
        timer.start(max(1, self._update_timeout_ms))
        return True

    def cancel_update(self) -> None:
        active = self._update_active
        if active is None:
            return
        self._release_update(active, abort=True)

    def _timeout_update(self, active: dict) -> None:
        if self._update_active is not active:
            return
        self._release_update(active, abort=True)
        self.update_checked.emit(None)

    def _finish_update(self, active: dict) -> None:
        if self._update_active is not active:
            return
        reply = active["reply"]
        info = None
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() == QNetworkReply.NoError and status == 200:
            try:
                info = parse_update_info(json.loads(bytes(reply.readAll()).decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                logger.debug("Invalid update response", exc_info=True)
        self._release_update(active)
        self.update_checked.emit(info)

    def _release_update(self, active: dict, *, abort: bool = False) -> None:
        if self._update_active is not active:
            return
        self._update_active = None
        active["timer"].stop()
        active["timer"].deleteLater()
        reply = active["reply"]
        if abort and reply.isRunning():
            reply.abort()
        reply.deleteLater()

    def cancel_all(self) -> None:
        self.cancel_service()
        self.cancel_model_capability()
        self.cancel_update()
