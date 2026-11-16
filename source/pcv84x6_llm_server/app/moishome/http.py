"""httpx helpers + platform response-envelope unpacking."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx


class MoishomeApiError(RuntimeError):
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


# Back-compat alias used by the first draft of this module / CLI.
DeviceApiError = MoishomeApiError


def build_url(api_root: str, path: str, query: dict[str, str] | None = None) -> str:
    path = path if path.startswith("/") else f"/{path}"
    url = f"{api_root.rstrip('/')}{path}"
    if query:
        filtered = {k: v for k, v in query.items() if v}
        if filtered:
            url = f"{url}?{urlencode(filtered)}"
    return url


def unpack_envelope(resp: httpx.Response) -> Any:
    """Return ``data`` from ``{ok, data|error, requestId}`` or raise ``MoishomeApiError``."""
    request_id = resp.headers.get("x-request-id", "")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise MoishomeApiError(
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
            raise MoishomeApiError(
                message,
                code=code,
                status=resp.status_code,
                request_id=request_id,
                body=payload,
            )
        if resp.status_code >= 400:
            raise MoishomeApiError(
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
        raise MoishomeApiError(
            message,
            code=code or "HTTP_ERROR",
            status=resp.status_code,
            request_id=request_id,
            body=payload,
        )
    return payload


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        resp = client.request(method, url, json=json_body, headers=headers)
    except httpx.HTTPError as exc:
        raise MoishomeApiError(f"transport error: {exc}", code="TRANSPORT") from exc
    return unpack_envelope(resp)
