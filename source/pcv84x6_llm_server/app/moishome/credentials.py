"""Persist formal device credentials after activate (atomic replace)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class DeviceCredentials:
    serial: str
    credential: str
    device_id: str = ""
    credential_prefix: str = ""
    activated_at: str = ""
    stock_status: str = ""
    binding_status: str = ""
    customer_bound: bool = False
    config_version: int = 0
    updated_at: str = ""
    extra: dict[str, Any] | None = None

    @classmethod
    def from_activate_data(
        cls,
        data: dict[str, Any],
        *,
        config_version: int = 0,
    ) -> "DeviceCredentials":
        credential = str(data.get("credential") or "")
        serial = str(data.get("serial") or "")
        if not credential:
            raise ValueError("activate response missing credential")
        if not serial:
            raise ValueError("activate response missing serial")
        prefix = str(data.get("credentialPrefix") or data.get("credential_prefix") or "")
        if not prefix:
            prefix = credential[:16]
        known_keys = {
            "serial",
            "credential",
            "deviceId",
            "device_id",
            "credentialPrefix",
            "credential_prefix",
            "activatedAt",
            "activated_at",
            "stockStatus",
            "stock_status",
            "bindingStatus",
            "binding_status",
            "customerBound",
            "customer_bound",
            "configVersion",
            "config_version",
            "updatedAt",
            "updated_at",
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}
        return cls(
            serial=serial,
            credential=credential,
            device_id=str(data.get("deviceId") or data.get("device_id") or ""),
            credential_prefix=prefix,
            activated_at=str(data.get("activatedAt") or data.get("activated_at") or ""),
            stock_status=str(data.get("stockStatus") or data.get("stock_status") or ""),
            binding_status=str(data.get("bindingStatus") or data.get("binding_status") or ""),
            customer_bound=bool(data.get("customerBound", data.get("customer_bound", False))),
            config_version=int(
                data.get("configVersion", data.get("config_version", config_version)) or 0
            ),
            updated_at=_utc_now(),
            extra=extra or None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serial": self.serial,
            "deviceId": self.device_id,
            "credential": self.credential,
            "credentialPrefix": self.credential_prefix,
            "activatedAt": self.activated_at,
            "stockStatus": self.stock_status,
            "bindingStatus": self.binding_status,
            "customerBound": self.customer_bound,
            "configVersion": self.config_version,
            "updatedAt": self.updated_at or _utc_now(),
        }
        if self.extra:
            payload["extra"] = self.extra
        return payload

    def public_dict(self) -> dict[str, Any]:
        """Safe summary for logs / CLI (no full credential)."""
        return {
            "serial": self.serial,
            "deviceId": self.device_id,
            "credentialPrefix": self.credential_prefix or (self.credential[:12] + "…"),
            "activatedAt": self.activated_at,
            "stockStatus": self.stock_status,
            "bindingStatus": self.binding_status,
            "customerBound": self.customer_bound,
            "configVersion": self.config_version,
            "updatedAt": self.updated_at,
        }


class CredentialStore:
    """JSON credential file under ``<data_dir>/state/moishome_device.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> DeviceCredentials | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("failed to read credentials %s: %s", self.path, exc)
            raise
        if not isinstance(raw, dict):
            raise ValueError(f"credentials file {self.path} is not an object")
        return DeviceCredentials(
            serial=str(raw.get("serial") or ""),
            credential=str(raw.get("credential") or ""),
            device_id=str(raw.get("deviceId") or raw.get("device_id") or ""),
            credential_prefix=str(
                raw.get("credentialPrefix") or raw.get("credential_prefix") or ""
            ),
            activated_at=str(raw.get("activatedAt") or raw.get("activated_at") or ""),
            stock_status=str(raw.get("stockStatus") or raw.get("stock_status") or ""),
            binding_status=str(
                raw.get("bindingStatus") or raw.get("binding_status") or ""
            ),
            customer_bound=bool(raw.get("customerBound", raw.get("customer_bound", False))),
            config_version=int(raw.get("configVersion", raw.get("config_version", 0)) or 0),
            updated_at=str(raw.get("updatedAt") or raw.get("updated_at") or ""),
            extra=raw.get("extra") if isinstance(raw.get("extra"), dict) else None,
        )

    def save(self, creds: DeviceCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not creds.updated_at:
            creds.updated_at = _utc_now()
        payload = creds.to_dict()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        logger.info(
            "persisted MoisHome credentials for serial=%s prefix=%s path=%s",
            creds.serial,
            creds.credential_prefix,
            self.path,
        )

    def update_config_version(self, version: int) -> DeviceCredentials | None:
        creds = self.load()
        if creds is None:
            return None
        creds.config_version = int(version)
        creds.updated_at = _utc_now()
        self.save(creds)
        return creds

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
