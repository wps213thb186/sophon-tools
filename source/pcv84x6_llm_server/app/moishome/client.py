"""``DeviceApiClient`` — MoisHome ``/device-api/*`` surface."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from .config import OpenPlatformConfig
from .credentials import CredentialStore, DeviceCredentials
from .http import MoishomeApiError, build_url, request_json
from .identity import resolve_identity

logger = logging.getLogger(__name__)

# Re-export for callers that import errors from client.
DeviceApiError = MoishomeApiError


class DeviceApiClient:
    """Minimal sync client for activate / heartbeat / shadow / catalog / entitlement.

    Optional library: constructing it does not touch the offline LLM service.
    """

    def __init__(
        self,
        cfg: OpenPlatformConfig,
        *,
        store: CredentialStore | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        self.api_root = cfg.api_root()
        self.store = store or (
            CredentialStore(cfg.credentials_path)
            if cfg.credentials_path is not None
            else None
        )
        self._creds = self.store.load() if self.store is not None else None
        identity = resolve_identity(cfg)
        self.serial = (self._creds.serial if self._creds else "") or identity.serial or cfg.serial
        self.sku = identity.sku or cfg.sku
        self.model = identity.model or cfg.model
        self._client = httpx.Client(
            timeout=cfg.timeout_s,
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": "cv84x6-moishome-device-api/1.0",
            },
        )

    @classmethod
    def from_env(
        cls,
        *,
        state_dir: Path | None = None,
        data_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> "DeviceApiClient":
        cfg = OpenPlatformConfig.from_env(state_dir=state_dir, data_dir=data_dir)
        store = CredentialStore(cfg.credentials_path) if cfg.credentials_path else None
        return cls(cfg, store=store, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DeviceApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- credential helpers -------------------------------------------------

    @property
    def credentials(self) -> DeviceCredentials | None:
        return self._creds

    @property
    def config_version(self) -> int:
        if self._creds is not None:
            return int(self._creds.config_version)
        return int(self.cfg.config_version)

    def _require_activated(self) -> DeviceCredentials:
        if self._creds is None or not self._creds.credential:
            raise MoishomeApiError(
                "device not activated; call activate() first",
                code="UNAUTHENTICATED",
            )
        return self._creds

    def _auth_headers(self, serial: str | None = None) -> dict[str, str]:
        creds = self._require_activated()
        sn = serial or self.serial or creds.serial
        if not sn:
            raise MoishomeApiError("serial missing for authenticated call", code="VALIDATION_FAILED")
        return {
            "Authorization": f"Bearer {creds.credential}",
            "x-device-serial": sn,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        auth: bool = False,
        serial: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if auth:
            headers.update(self._auth_headers(serial))
        url = build_url(self.api_root, path, query)
        return request_json(self._client, method, url, json_body=json_body, headers=headers)

    # -- public API (design names) ------------------------------------------

    def activate(
        self,
        serial: str | None = None,
        temp_key: str | None = None,
    ) -> DeviceCredentials:
        sn = (serial or self.serial or self.cfg.serial or "").strip()
        key = (temp_key if temp_key is not None else self.cfg.temp_key) or ""
        if not sn or not key:
            raise MoishomeApiError("serial and tempKey required", code="VALIDATION_FAILED")
        if len(key) < 16:
            raise MoishomeApiError("tempKey too short (<16)", code="VALIDATION_FAILED")
        data = self._request(
            "POST",
            "/device-api/activate",
            json_body={"serial": sn, "tempKey": key},
        )
        if not isinstance(data, dict):
            raise MoishomeApiError("activate returned unexpected payload", body=data)
        creds = DeviceCredentials.from_activate_data(
            data, config_version=self.cfg.config_version
        )
        self.serial = creds.serial
        self._creds = creds
        if self.store is not None:
            self.store.save(creds)
        else:
            logger.warning("activate succeeded but no CredentialStore configured; not persisted")
        return creds

    def heartbeat(
        self,
        *,
        serial: str | None = None,
        firmware: str | None = None,
        config_version: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        sn = serial or self.serial
        body: dict[str, Any] = {"serial": sn}
        fw = self.cfg.firmware if firmware is None else firmware
        if fw:
            body["firmware"] = fw
            body["firmwareVersion"] = fw
        cv = self.config_version if config_version is None else config_version
        body["configVersion"] = cv
        attrs = dict(attributes or {})
        if self.sku and "sku" not in attrs:
            attrs["sku"] = self.sku
        if self.model and "model" not in attrs:
            attrs["model"] = self.model
        if attrs:
            body["attributes"] = attrs
        return self._request(
            "POST",
            "/device-api/heartbeat",
            json_body=body,
            auth=True,
            serial=sn,
        )

    def shadow(self, *, serial: str | None = None) -> Any:
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/shadow",
            query={"serial": sn} if sn else None,
            auth=True,
            serial=sn,
        )

    def ack_ota(
        self,
        job_id: str,
        status: str,
        *,
        reason: str = "",
        serial: str | None = None,
    ) -> Any:
        allowed = {"downloading", "applied", "failed"}
        if not job_id or status not in allowed:
            raise MoishomeApiError(
                "jobId and status (downloading|applied|failed) required",
                code="VALIDATION_FAILED",
            )
        sn = serial or self.serial
        body: dict[str, Any] = {"jobId": job_id, "status": status}
        if reason:
            body["reason"] = reason
        return self._request(
            "POST",
            "/device-api/ota/ack",
            json_body=body,
            auth=True,
            serial=sn,
        )

    def ack_config(self, version: int, *, serial: str | None = None) -> Any:
        sn = serial or self.serial
        data = self._request(
            "POST",
            "/device-api/config/ack",
            json_body={"version": int(version)},
            auth=True,
            serial=sn,
        )
        if self.store is not None:
            updated = self.store.update_config_version(int(version))
            if updated is not None:
                self._creds = updated
        elif self._creds is not None:
            self._creds.config_version = int(version)
        return data

    def cloud_catalog(
        self,
        *,
        serial: str | None = None,
        model: str = "",
        sku: str = "",
        isv: str = "",
        types: str = "",
    ) -> Any:
        """Cloud content directory — **not** the local ``GET /api/catalog`` dfss list."""
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/catalog",
            query={
                "serial": sn or "",
                "model": model or self.model or "",
                "sku": sku or self.sku or "",
                "isv": isv,
                "types": types,
            },
            auth=True,
            serial=sn,
        )

    def entitlement_check(
        self,
        *,
        serial: str | None = None,
        capability: str = "",
        content_id: str = "",
        content_type: str = "",
        product_id: str = "",
    ) -> Any:
        sn = serial or self.serial
        body: dict[str, Any] = {"serial": sn}
        if capability:
            body["capability"] = capability
        if content_id:
            body["contentId"] = content_id
        if content_type:
            body["contentType"] = content_type
        if product_id:
            body["productId"] = product_id
        return self._request(
            "POST",
            "/device-api/entitlement/check",
            json_body=body,
            auth=True,
            serial=sn,
        )

    def compat(self, *, serial: str | None = None) -> Any:
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/compat",
            query={"serial": sn} if sn else None,
            auth=True,
            serial=sn,
        )

    # -- draft aliases (kept for CLI / earlier helpers) ---------------------

    def get_shadow(self, **kwargs: Any) -> Any:
        return self.shadow(**kwargs)

    def get_catalog(self, **kwargs: Any) -> Any:
        return self.cloud_catalog(**kwargs)

    def check_entitlement(self, **kwargs: Any) -> Any:
        return self.entitlement_check(**kwargs)

    def get_compat(self, **kwargs: Any) -> Any:
        return self.compat(**kwargs)
