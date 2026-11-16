"""HTTP client for MoisHome ``/device-api/*`` (envelope-aware)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import OpenPlatformConfig
from .credentials import DeviceCredentials

logger = logging.getLogger(__name__)


class DeviceApiError(RuntimeError):
    """Open-platform device-api failure (transport, HTTP, or ``ok: false``)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status: int | None = None,
        request_id: str = "",
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.body = body


class DeviceApiClient:
    """Minimal sync client; safe to call from a thread or a short CLI."""

    def __init__(
        self,
        cfg: OpenPlatformConfig,
        *,
        credential: str = "",
        serial: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        self.base_url = cfg.require_base_url()
        self.credential = credential
        self.serial = serial
        self._client = httpx.Client(
            timeout=cfg.timeout_s,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "cv84x6-moishome-device-api/1.0"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DeviceApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def use_credentials(self, creds: DeviceCredentials) -> None:
        self.credential = creds.credential
        self.serial = creds.serial

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{path}"
        if query:
            filtered = {k: v for k, v in query.items() if v}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        return url

    def _auth_headers(self, serial: str | None = None) -> dict[str, str]:
        sn = serial or self.serial
        if not self.credential:
            raise DeviceApiError("formal credential missing; activate first", code="UNAUTHENTICATED")
        if not sn:
            raise DeviceApiError("serial missing for authenticated call", code="VALIDATION_FAILED")
        return {
            "Authorization": f"Bearer {self.credential}",
            "x-device-serial": sn,
        }

    def _parse(self, resp: httpx.Response) -> Any:
        request_id = resp.headers.get("x-request-id", "")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DeviceApiError(
                f"non-JSON response ({resp.status_code})",
                status=resp.status_code,
                request_id=request_id,
                body=resp.text[:500],
            ) from exc

        if isinstance(payload, dict) and "ok" in payload:
            request_id = str(payload.get("requestId") or request_id)
            if not payload.get("ok"):
                err = payload.get("error") or {}
                if not isinstance(err, dict):
                    err = {"message": str(err)}
                code = str(err.get("code") or "ERROR")
                message = str(err.get("message") or code)
                raise DeviceApiError(
                    message,
                    code=code,
                    status=resp.status_code,
                    request_id=request_id,
                    body=payload,
                )
            if resp.status_code >= 400:
                raise DeviceApiError(
                    f"HTTP {resp.status_code} with ok envelope",
                    code="HTTP_ERROR",
                    status=resp.status_code,
                    request_id=request_id,
                    body=payload,
                )
            return payload.get("data")

        if resp.status_code >= 400:
            code = ""
            message = f"HTTP {resp.status_code}"
            if isinstance(payload, dict):
                code = str(payload.get("code") or "")
                message = str(payload.get("message") or message)
            raise DeviceApiError(
                message,
                code=code or "HTTP_ERROR",
                status=resp.status_code,
                request_id=request_id,
                body=payload,
            )
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = False,
        serial: str | None = None,
    ) -> Any:
        hdrs = dict(headers or {})
        if auth:
            hdrs.update(self._auth_headers(serial))
        url = self._url(path, query)
        try:
            resp = self._client.request(method, url, json=json_body, headers=hdrs)
        except httpx.HTTPError as exc:
            raise DeviceApiError(f"transport error: {exc}", code="TRANSPORT") from exc
        return self._parse(resp)

    # -- public API ---------------------------------------------------------

    def activate(self, serial: str, temp_key: str) -> dict[str, Any]:
        if not serial or not temp_key:
            raise DeviceApiError("serial and tempKey required", code="VALIDATION_FAILED")
        if len(temp_key) < 16:
            raise DeviceApiError("tempKey too short (<16)", code="VALIDATION_FAILED")
        data = self._request(
            "POST",
            "/device-api/activate",
            json_body={"serial": serial, "tempKey": temp_key},
        )
        if not isinstance(data, dict):
            raise DeviceApiError("activate returned unexpected payload", body=data)
        return data

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
        fw = firmware if firmware is not None else self.cfg.firmware
        if fw:
            body["firmware"] = fw
            body["firmwareVersion"] = fw
        cv = self.cfg.config_version if config_version is None else config_version
        body["configVersion"] = cv
        if attributes:
            body["attributes"] = attributes
        return self._request(
            "POST",
            "/device-api/heartbeat",
            json_body=body,
            auth=True,
            serial=sn,
        )

    def get_shadow(self, *, serial: str | None = None) -> Any:
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/shadow",
            query={"serial": sn} if sn else None,
            auth=True,
            serial=sn,
        )

    def get_catalog(
        self,
        *,
        serial: str | None = None,
        model: str = "",
        sku: str = "",
        isv: str = "",
        types: str = "",
    ) -> Any:
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/catalog",
            query={
                "serial": sn or "",
                "model": model,
                "sku": sku,
                "isv": isv,
                "types": types,
            },
            auth=True,
            serial=sn,
        )

    def check_entitlement(
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

    def ack_ota(
        self,
        job_id: str,
        status: str,
        *,
        reason: str = "",
        serial: str | None = None,
    ) -> Any:
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
        return self._request(
            "POST",
            "/device-api/config/ack",
            json_body={"version": version},
            auth=True,
            serial=sn,
        )

    def get_compat(self, *, serial: str | None = None) -> Any:
        sn = serial or self.serial
        return self._request(
            "GET",
            "/device-api/compat",
            query={"serial": sn} if sn else None,
            auth=True,
            serial=sn,
        )
