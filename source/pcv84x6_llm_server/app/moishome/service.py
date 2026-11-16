"""Optional in-process bridge: attach to FastAPI without touching inference."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .client import DeviceApiClient
from .config import OpenPlatformConfig
from .credentials import CredentialStore
from .http import MoishomeApiError
from .identity import resolve_identity
from .runtime import HeartbeatOutcome, run_cycle

logger = logging.getLogger(__name__)


@dataclass
class MoishomeBridge:
    """Service-side handle for the open-platform client.

    Constructed even when disabled (``enabled=False``) so ``/moishome/status``
    can explain why cloud calls are unavailable. Sync HTTP runs in a worker
    thread; the asyncio heartbeat task only schedules those calls.
    """

    cfg: OpenPlatformConfig
    store: CredentialStore | None
    transport: httpx.BaseTransport | None = None
    last_outcome: HeartbeatOutcome | None = None
    last_error: str = ""
    _stop: threading.Event = field(default_factory=threading.Event)
    _task: asyncio.Task | None = field(default=None, repr=False)

    @classmethod
    def from_state_dir(
        cls,
        state_dir: Path,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> "MoishomeBridge":
        cfg = OpenPlatformConfig.from_env(state_dir=state_dir)
        store = CredentialStore(cfg.credentials_path) if cfg.credentials_path else None
        return cls(cfg=cfg, store=store, transport=transport)

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    def status(self) -> dict[str, Any]:
        identity = resolve_identity(self.cfg)
        creds = self.store.load() if self.store is not None else None
        return {
            "enabled": self.enabled,
            "baseUrl": self.cfg.base_url or None,
            "apiRoot": self.cfg.api_root() if self.enabled else None,
            "heartbeatEnabled": self.cfg.heartbeat_enabled,
            "heartbeatIntervalS": self.cfg.heartbeat_interval_s,
            "heartbeatRunning": bool(self._task and not self._task.done()),
            "identity": {
                "serial": identity.serial or None,
                "sku": identity.sku or None,
                "model": identity.model or None,
            },
            "credentialsPath": str(self.store.path) if self.store else None,
            "activated": bool(creds and creds.credential),
            "credentials": creds.public_dict() if creds and creds.credential else None,
            "lastError": self.last_error or None,
            "lastHeartbeatErrors": (
                list(self.last_outcome.errors) if self.last_outcome else None
            ),
        }

    def _client(self) -> DeviceApiClient:
        if not self.enabled:
            raise MoishomeApiError(
                "MoisHome disabled: set MOISHOME_BASE_URL to enable",
                code="DISABLED",
            )
        return DeviceApiClient(self.cfg, store=self.store, transport=self.transport)

    def ensure_activated(self) -> dict[str, Any]:
        with self._client() as client:
            if client.credentials and client.credentials.credential:
                return client.credentials.public_dict()
            if not self.cfg.temp_key:
                raise MoishomeApiError(
                    "not activated and MOISHOME_TEMP_KEY unset",
                    code="UNAUTHENTICATED",
                )
            return client.activate().public_dict()

    def activate(self) -> dict[str, Any]:
        with self._client() as client:
            return client.activate().public_dict()

    def heartbeat(self) -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.heartbeat()

    def shadow(self) -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.shadow()

    def cloud_catalog(self, **kwargs: Any) -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.cloud_catalog(**kwargs)

    def entitlement_check(self, **kwargs: Any) -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.entitlement_check(**kwargs)

    def ack_ota(self, job_id: str, status: str, reason: str = "") -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.ack_ota(job_id, status, reason=reason)

    def ack_config(self, version: int) -> Any:
        with self._client() as client:
            self.ensure_client_ready(client)
            return client.ack_config(version)

    def ensure_client_ready(self, client: DeviceApiClient) -> None:
        if client.credentials and client.credentials.credential:
            return
        if self.cfg.temp_key:
            client.activate()
            return
        raise MoishomeApiError(
            "not activated and MOISHOME_TEMP_KEY unset",
            code="UNAUTHENTICATED",
        )

    def run_once(self) -> HeartbeatOutcome:
        with self._client() as client:
            self.ensure_client_ready(client)
            outcome = run_cycle(client, auto_ack=True)
            self.last_outcome = outcome
            if outcome.errors:
                self.last_error = "; ".join(outcome.errors)
            else:
                self.last_error = ""
            return outcome

    async def start_heartbeat(self) -> None:
        if not self.enabled or not self.cfg.heartbeat_enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._heartbeat_loop(), name="moishome-heartbeat")
        logger.info(
            "MoisHome heartbeat loop started (interval=%ss)",
            self.cfg.heartbeat_interval_s,
        )

    async def stop_heartbeat(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("MoisHome heartbeat loop stopped")

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except MoishomeApiError as exc:
                self.last_error = f"{exc.code}: {exc}"
                logger.warning("MoisHome heartbeat failed: %s", self.last_error)
            except Exception as exc:  # pragma: no cover - defensive
                self.last_error = str(exc)
                logger.exception("MoisHome heartbeat crashed")
            # Sleep in small slices so stop_heartbeat cancels promptly.
            remaining = float(self.cfg.heartbeat_interval_s)
            while remaining > 0 and not self._stop.is_set():
                step = min(0.5, remaining)
                await asyncio.sleep(step)
                remaining -= step
