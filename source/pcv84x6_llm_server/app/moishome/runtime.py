"""Periodic heartbeat and OTA/config acknowledgement helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .client import DeviceApiClient
from .config import OpenPlatformConfig
from .credentials import CredentialStore
from .http import MoishomeApiError
from .identity import DeviceIdentity, require_serial, resolve_identity

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatOutcome:
    heartbeat: Any = None
    shadow: Any = None
    ota_ack: Any = None
    config_ack: Any = None
    errors: list[str] = field(default_factory=list)


def _pending_ota_job_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("pendingOta", "pending_ota"):
        pending = payload.get(key)
        if isinstance(pending, dict):
            job_id = pending.get("jobId") or pending.get("id") or ""
            if job_id:
                return str(job_id)
        if isinstance(pending, list) and pending:
            first = pending[0]
            if isinstance(first, dict):
                job_id = first.get("jobId") or first.get("id") or ""
                if job_id:
                    return str(job_id)
    return ""


def _config_version_to_ack(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    cfg = payload.get("config")
    if isinstance(cfg, dict) and "version" in cfg:
        try:
            return int(cfg["version"])
        except (TypeError, ValueError):
            return None
    return None


def run_cycle(
    client: DeviceApiClient,
    identity: DeviceIdentity | None = None,
    *,
    pull_shadow: bool = True,
    auto_ack: bool = True,
    ota_status: str = "applied",
) -> HeartbeatOutcome:
    """One heartbeat (+ optional shadow / ACK). Does not sleep."""

    out = HeartbeatOutcome()
    ident = identity or DeviceIdentity(serial=client.serial, sku=client.sku, model=client.model)
    try:
        out.heartbeat = client.heartbeat(serial=ident.serial or None)
    except MoishomeApiError as exc:
        out.errors.append(f"heartbeat: {exc.code or 'ERROR'}: {exc}")
        return out

    if pull_shadow:
        try:
            out.shadow = client.shadow(serial=ident.serial or None)
        except MoishomeApiError as exc:
            out.errors.append(f"shadow: {exc.code or 'ERROR'}: {exc}")

    if not auto_ack:
        return out

    for src in (out.heartbeat, out.shadow):
        job_id = _pending_ota_job_id(src)
        if job_id and out.ota_ack is None:
            try:
                out.ota_ack = client.ack_ota(job_id, ota_status, serial=ident.serial or None)
            except MoishomeApiError as exc:
                out.errors.append(f"ota-ack: {exc.code or 'ERROR'}: {exc}")
        version = _config_version_to_ack(src)
        if version is not None and out.config_ack is None:
            try:
                out.config_ack = client.ack_config(version, serial=ident.serial or None)
            except MoishomeApiError as exc:
                out.errors.append(f"config-ack: {exc.code or 'ERROR'}: {exc}")
    return out


class HeartbeatLoop:
    """Blocking loop for smoke / standalone process use."""

    def __init__(
        self,
        cfg: OpenPlatformConfig,
        *,
        store: CredentialStore | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        transport=None,
    ) -> None:
        self.cfg = cfg
        if store is None:
            if cfg.credentials_path is None:
                raise ValueError("credentials_path required for HeartbeatLoop")
            store = CredentialStore(cfg.credentials_path)
        self.store = store
        self.sleep_fn = sleep_fn
        self.transport = transport

    def tick(self) -> HeartbeatOutcome:
        identity = resolve_identity(self.cfg)
        require_serial(identity)
        with DeviceApiClient(self.cfg, store=self.store, transport=self.transport) as client:
            if client.credentials is None or not client.credentials.credential:
                if not self.cfg.temp_key:
                    raise MoishomeApiError(
                        "no persisted credential and MOISHOME_TEMP_KEY unset",
                        code="UNAUTHENTICATED",
                    )
                client.activate(identity.serial, self.cfg.temp_key)
            return run_cycle(client, identity)

    def run(self, *, once: bool = False, max_ticks: int | None = None) -> None:
        ticks = 0
        while True:
            outcome = self.tick()
            if outcome.errors:
                for err in outcome.errors:
                    logger.warning("heartbeat cycle: %s", err)
            else:
                logger.info("heartbeat cycle ok")
            ticks += 1
            if once or (max_ticks is not None and ticks >= max_ticks):
                return
            self.sleep_fn(self.cfg.heartbeat_interval_s)
