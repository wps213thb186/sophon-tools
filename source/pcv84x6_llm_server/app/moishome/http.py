"""HTTP transport for MoisHome device-api envelopes."""

from __future__ import annotations

from typing import Any

import httpx


class MoishomeApiError(Exception):
    """Platform returned ``ok: false`` or a non-envelope HTTP failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 0,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.request_id = request_id
        self.body = body


class MoishomeHttp:
    """Thin httpx wrapper: builds ``/api/v1`` URLs and unwraps ``{ok,data|error}``."""

    def __init__(
        self,
        api_root: str,
        *,
        timeout_s: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_root = api_root.rstrip("/")
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            kwargs: dict[str, Any] = {"timeout": timeout_s, "follow_redirects": True}
            if transport is not None:
                kwargs["transport"] = transport
            self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MoishomeHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.api_root}/{path.lstrip('/')}"
        resp = self._client.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
        )
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        request_id = None
        try:
            payload = resp.json()
        except ValueError:
            raise MoishomeApiError(
                "HTTP_ERROR",
                f"non-JSON response ({resp.status_code})",
                status=resp.status_code,
                body=resp.text[:500],
            ) from None

        if isinstance(payload, dict):
            request_id = payload.get("requestId")
            if payload.get("ok") is True:
                return payload.get("data")
            if payload.get("ok") is False:
                err = payload.get("error") or {}
                if not isinstance(err, dict):
                    err = {"code": "HTTP_ERROR", "message": str(err)}
                raise MoishomeApiError(
                    str(err.get("code") or "HTTP_ERROR"),
                    str(err.get("message") or resp.reason_phrase or "request failed"),
                    status=resp.status_code,
                    request_id=request_id if isinstance(request_id, str) else None,
                    body=payload,
                )

        if resp.is_error:
            raise MoishomeApiError(
                "HTTP_ERROR",
                f"HTTP {resp.status_code}",
                status=resp.status_code,
                request_id=request_id if isinstance(request_id, str) else None,
                body=payload,
            )
        return payload
