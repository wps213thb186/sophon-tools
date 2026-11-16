"""Management surface for MoisHome device-api (NOT under ``/api/catalog``).

Mounted at ``/moishome/*`` so it does not enter the ``/api`` OpenAPI docs table.
Admin cookie or admin-scoped bearer key required (same gate as ``/api``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.auth import require_admin
from ..moishome.http import MoishomeApiError
from ..moishome.service import MoishomeBridge

router = APIRouter(prefix="/moishome", tags=["moishome"])


def _bridge(request: Request) -> MoishomeBridge:
    bridge = getattr(request.app.state, "moishome", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="MoisHome bridge not attached")
    return bridge


def _map_error(exc: MoishomeApiError) -> None:
    status = exc.status or 400
    if exc.code == "DISABLED":
        status = 503
    elif exc.code in ("UNAUTHENTICATED", "KEY_INVALID"):
        status = 401
    elif exc.code == "VALIDATION_FAILED":
        status = 422
    elif exc.code == "RATE_LIMITED":
        status = 429
    elif status < 400:
        status = 502
    detail = f"[{exc.code or 'ERROR'}] {exc}"
    if exc.request_id:
        detail = f"{detail} (requestId={exc.request_id})"
    raise HTTPException(status_code=status, detail=detail)


@router.get("/status")
async def moishome_status(request: Request, _=Depends(require_admin)):
    return _bridge(request).status()


@router.post("/activate")
async def moishome_activate(request: Request, _=Depends(require_admin)):
    try:
        return {"ok": True, "data": _bridge(request).activate()}
    except MoishomeApiError as exc:
        _map_error(exc)


@router.post("/heartbeat")
async def moishome_heartbeat(request: Request, _=Depends(require_admin)):
    try:
        return {"ok": True, "data": _bridge(request).heartbeat()}
    except MoishomeApiError as exc:
        _map_error(exc)


@router.get("/shadow")
async def moishome_shadow(request: Request, _=Depends(require_admin)):
    try:
        return {"ok": True, "data": _bridge(request).shadow()}
    except MoishomeApiError as exc:
        _map_error(exc)


@router.get("/cloud-catalog")
async def moishome_cloud_catalog(
    request: Request,
    model: str = "",
    sku: str = "",
    isv: str = "",
    types: str = "",
    _=Depends(require_admin),
):
    """Cloud content directory — distinct from local ``GET /api/catalog``."""
    try:
        return {
            "ok": True,
            "data": _bridge(request).cloud_catalog(
                model=model, sku=sku, isv=isv, types=types
            ),
        }
    except MoishomeApiError as exc:
        _map_error(exc)


class EntitlementBody(BaseModel):
    capability: str = ""
    contentId: str = ""
    contentType: str = ""
    productId: str = ""


@router.post("/entitlement-check")
async def moishome_entitlement_check(
    body: EntitlementBody,
    request: Request,
    _=Depends(require_admin),
):
    try:
        return {
            "ok": True,
            "data": _bridge(request).entitlement_check(
                capability=body.capability,
                content_id=body.contentId,
                content_type=body.contentType,
                product_id=body.productId,
            ),
        }
    except MoishomeApiError as exc:
        _map_error(exc)


class OtaAckBody(BaseModel):
    jobId: str
    status: str = Field(description="downloading|applied|failed")
    reason: str = ""


@router.post("/ota-ack")
async def moishome_ota_ack(body: OtaAckBody, request: Request, _=Depends(require_admin)):
    try:
        return {
            "ok": True,
            "data": _bridge(request).ack_ota(body.jobId, body.status, reason=body.reason),
        }
    except MoishomeApiError as exc:
        _map_error(exc)


class ConfigAckBody(BaseModel):
    version: int


@router.post("/config-ack")
async def moishome_config_ack(
    body: ConfigAckBody, request: Request, _=Depends(require_admin)
):
    try:
        return {"ok": True, "data": _bridge(request).ack_config(body.version)}
    except MoishomeApiError as exc:
        _map_error(exc)


@router.post("/cycle")
async def moishome_cycle(request: Request, _=Depends(require_admin)):
    """One heartbeat (+ optional shadow/ACK) for smoke / ops."""
    try:
        outcome = _bridge(request).run_once()
        return {
            "ok": not outcome.errors,
            "heartbeat": outcome.heartbeat,
            "shadow": outcome.shadow,
            "otaAck": outcome.ota_ack,
            "configAck": outcome.config_ack,
            "errors": outcome.errors,
        }
    except MoishomeApiError as exc:
        _map_error(exc)
