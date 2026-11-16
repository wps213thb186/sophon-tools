"""Minimal MoisHome device-api client for the zetao board.

Contract: moishome-open-platform ``docs/zetao-device-api-contract.md``.
Cloud catalogue is ``cloud_catalog`` → ``GET /device-api/catalog``.
Never confuse with the local board route ``GET /api/catalog``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import MoishomeConfig, load_config
from .credentials import CredentialsStore, DeviceCredentials
from .http import MoishomeApiError, MoishomeHttp

# Local board catalogue (admin_api) — documented so callers do not mix them up.
LOCAL_BOARD_CATALOG_PATH = "/api/catalog"
CLOUD_DEVICE_CATALOG_PATH = "/device-api/catalog"


class DeviceApiClient:
    """SN activate + heartbeat/shadow/ACK + cloud catalog/entitlement."""

    def __init__(
        self,
        config: MoishomeConfig,
        *,
        store: CredentialsStore | None = None,
        http: MoishomeHttp | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not config.base_url:
            raise ValueError("MOISHOME_BASE_URL is required")
        self.config = config
        state_dir = config.state_dir or Path(".")
        self.store = store or CredentialsStore(state_dir)
        self._http = http or MoishomeHttp(
            config.api_root,
            timeout_s=config.timeout_s,
            transport=transport,
        )
        self._owns_http = http is None
        self._creds = self.store.load()

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        state_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> DeviceApiClient:
        return cls(load_config(env, state_dir=state_dir), transport=transport)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> DeviceApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def credentials(self) -> DeviceCredentials | None:
        return self._creds

    def is_activated(self) -> bool:
        return self._creds is not None and self._creds.activated()

    # -- auth helpers -------------------------------------------------------
    def _auth_headers(self, serial: str | None = None) -> dict[str, str]:
        creds = self._creds
        if creds is None or not creds.credential:
            raise MoishomeApiError("KEY_INVALID", "device not activated; call activate() first")
        sn = (serial or creds.serial or self.config.serial or "").strip()
        if not sn:
            raise MoishomeApiError("DEVICE_REQUIRED", "missing device serial")
        return {
            "Authorization": f"Bearer {creds.credential}",
            "x-device-serial": sn,
        }

    # -- 1. activate --------------------------------------------------------
    def activate(
        self,
        serial: str | None = None,
        temp_key: str | None = None,
        *,
        persist: bool = True,
    ) -> DeviceCredentials:
        sn = (serial or self.config.serial or "").strip()
        key = (temp_key or self.config.temp_key or "").strip()
        if not sn:
            raise MoishomeApiError("VALIDATION_FAILED", "缺少序列号", status=422)
        if not key:
            raise MoishomeApiError("VALIDATION_FAILED", "缺少临时密钥", status=422)
        if len(key) < 16:
            raise MoishomeApiError("VALIDATION_FAILED", "临时密钥至少 16 位", status=422)

        data = self._http.request(
            "POST",
            "/device-api/activate",
            json_body={"serial": sn, "tempKey": key},
        )
        if not isinstance(data, dict):
            raise MoishomeApiError("HTTP_ERROR", "activate response missing data object")
        creds = DeviceCredentials.from_activate_data(
            data,
            config_version=self.config.config_version,
        )
        if not creds.activated():
            raise MoishomeApiError("HTTP_ERROR", "activate response missing credential")
        if persist:
            self.store.save(creds)
        self._creds = creds
        return creds

    # -- 2. heartbeat / shadow / ACK ----------------------------------------
    def heartbeat(
        self,
        *,
        firmware: str | None = None,
        config_version: int | None = None,
        attributes: dict[str, Any] | None = None,
        serial: str | None = None,
    ) -> Any:
        creds = self._creds
        sn = (serial or (creds.serial if creds else "") or self.config.serial).strip()
        fw = (firmware if firmware is not None else self.config.firmware) or ""
        if config_version is None:
            if creds is not None:
                cv = creds.config_version
            else:
                cv = self.config.config_version
        else:
            cv = config_version
        attrs = dict(attributes or {})
        sku = self.config.sku
        if sku and "sku" not in attrs:
            attrs["sku"] = sku
        body: dict[str, Any] = {
            "serial": sn,
            "configVersion": cv,
        }
        if fw:
            body["firmware"] = fw
            body["firmwareVersion"] = fw
        if attrs:
            body["attributes"] = attrs
        return self._http.request(
            "POST",
            "/device-api/heartbeat",
            headers=self._auth_headers(sn),
            json_body=body,
        )

    def shadow(self, *, serial: str | None = None) -> Any:
        creds = self._creds
        sn = (serial or (creds.serial if creds else "") or self.config.serial).strip()
        return self._http.request(
            "GET",
            "/device-api/shadow",
            headers=self._auth_headers(sn),
            params={"serial": sn} if sn else None,
        )

    def ack_ota(self, job_id: str, status: str, reason: str = "") -> Any:
        if status not in ("downloading", "applied", "failed"):
            raise MoishomeApiError(
                "VALIDATION_FAILED",
                "状态只能是 downloading/applied/failed",
                status=422,
            )
        if not job_id:
            raise MoishomeApiError("VALIDATION_FAILED", "缺少 jobId", status=422)
        body: dict[str, Any] = {"jobId": job_id, "status": status}
        if reason:
            body["reason"] = reason
        return self._http.request(
            "POST",
            "/device-api/ota/ack",
            headers=self._auth_headers(),
            json_body=body,
        )

    def ack_config(self, version: int, *, persist: bool = True) -> Any:
        data = self._http.request(
            "POST",
            "/device-api/config/ack",
            headers=self._auth_headers(),
            json_body={"version": int(version)},
        )
        if persist:
            updated = self.store.update_config_version(int(version))
            if updated is not None:
                self._creds = updated
            elif self._creds is not None:
                self._creds.config_version = int(version)
        return data

    # -- 3. cloud catalog + entitlement (NOT local /api/catalog) ------------
    def cloud_catalog(
        self,
        *,
        model: str | None = None,
        sku: str | None = None,
        isv: str | None = None,
        types: str | None = None,
        serial: str | None = None,
    ) -> Any:
        """Fetch the *cloud* device catalogue.

        This calls ``GET /api/v1/device-api/catalog``. It is **not** the local
        board route ``GET /api/catalog`` (``LOCAL_BOARD_CATALOG_PATH``).
        """
        creds = self._creds
        sn = (serial or (creds.serial if creds else "") or self.config.serial).strip()
        params: dict[str, str] = {}
        if sn:
            params["serial"] = sn
        if model:
            params["model"] = model
        if sku:
            params["sku"] = sku
        elif self.config.sku:
            params["sku"] = self.config.sku
        if isv:
            params["isv"] = isv
        if types:
            params["types"] = types
        return self._http.request(
            "GET",
            CLOUD_DEVICE_CATALOG_PATH,
            headers=self._auth_headers(sn),
            params=params or None,
        )

    def entitlement_check(
        self,
        *,
        capability: str | None = None,
        content_id: str | None = None,
        content_type: str | None = None,
        product_id: str | None = None,
        serial: str | None = None,
    ) -> Any:
        creds = self._creds
        sn = (serial or (creds.serial if creds else "") or self.config.serial).strip()
        body: dict[str, Any] = {"serial": sn}
        if capability:
            body["capability"] = capability
        if content_id:
            body["contentId"] = content_id
        if content_type:
            body["contentType"] = content_type
        if product_id:
            body["productId"] = product_id
        return self._http.request(
            "POST",
            "/device-api/entitlement/check",
            headers=self._auth_headers(sn),
            json_body=body,
        )
