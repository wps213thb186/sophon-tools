"""Periodic heartbeat and OTA/config acknowledgement helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .client import DeviceApiClient, DeviceApiError
from .config import OpenPlatformConfig
from .credentials import CredentialStore, DeviceCredentials
from .identity import DeviceIdentity, require_serial, resolve_identity

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatOutcome:
    heartbeat: Any = None
    shadow: Any = None
    ota_ack: Any = None
    config_ack: Any = None
    errors: list[str] = field(default_factory=list)


def activate_and_persist(
    client: DeviceApiClient,
    store: CredentialStore,
    *,
    serial: str,
    temp_key: str,
) -> DeviceCredentials:
    data = client.activate(serial, temp_key)
    creds = DeviceCredentials.from_activate_data(data)
    store.save(creds)
    client.use_credentials(creds)
    return creds


def load_or_activate(
    cfg: OpenPlatformConfig,
    client: DeviceApiClient,
    store: CredentialStore,
    identity: DeviceIdentity,
) -> DeviceCredentials:
    existing = store.load()
    if existing and existing.credential and existing.serial:
        client.use_credentials(existing)
        return existing
    serial = require_serial(identity)
    if not cfg.temp_key:
        raise DeviceApiError(
            "no persisted credential and MOISHOME_TEMP_KEY unset",
            code="UNAUTHENTICATED",
        )
    return activate_and_persist(client, store, serial=serial, temp_key=cfg.temp_key)


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
    cfg: OpenPlatformConfig,
    identity: DeviceIdentity,
    *,
    pull_shadow: bool = True,
    auto_ack: bool = True,
    ota_status: str = "applied",
) -> HeartbeatOutcome:
    """One heartbeat (+ optional shadow / ACK). Does not sleep."""

    out = HeartbeatOutcome()
    attrs: dict[str, Any] = {}
    if identity.sku:
        attrs["sku"] = identity.sku
    if identity.model:
        attrs["model"] = identity.model
    try:
        out.heartbeat = client.heartbeat(
            serial=identity.serial or None,
            firmware=cfg.firmware,
            config_version=cfg.config_version,
            attributes=attrs or None,
        )
    except DeviceApiError as exc:
        out.errors.append(f"heartbeat: {exc.code or 'ERROR'}: {exc}")
        return out

    if pull_shadow:
        try:
            out.shadow = client.get_shadow(serial=identity.serial or None)
        except DeviceApiError as exc:
            out.errors.append(f"shadow: {exc.code or 'ERROR'}: {exc}")

    if not auto_ack:
        return out

    sources = [out.heartbeat, out.shadow]
    for src in sources:
        job_id = _pending_ota_job_id(src)
        if job_id and out.ota_ack is None:
            try:
                out.ota_ack = client.ack_ota(job_id, ota_status, serial=identity.serial or None)
            except DeviceApiError as exc:
                out.errors.append(f"ota-ack: {exc.code or 'ERROR'}: {exc}")
        version = _config_version_to_ack(src)
        if version is not None and out.config_ack is None:
            try:
                out.config_ack = client.ack_config(version, serial=identity.serial or None)
            except DeviceApiError as exc:
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
    ) -> None:
        self.cfg = cfg
        if store is None:
            if cfg.credentials_path is None:
                raise ValueError("credentials_path required for HeartbeatLoop")
            store = CredentialStore(cfg.credentials_path)
        self.store = store
        self.sleep_fn = sleep_fn

    def tick(self, client: DeviceApiClient | None = None) -> HeartbeatOutcome:
        identity = resolve_identity(self.cfg)
        require_serial(identity)
        own = client is None
        client = client or DeviceApiClient(self.cfg)
        try:
            load_or_activate(self.cfg, client, self.store, identity)
            return run_cycle(client, self.cfg, identity)
        finally:
            if own:
                client.close()

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
