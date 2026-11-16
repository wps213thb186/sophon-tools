"""Persist formal device credentials after activate (atomic replace)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    extra: dict[str, Any] | None = None

    @classmethod
    def from_activate_data(cls, data: dict[str, Any]) -> "DeviceCredentials":
        mapped = {
            "serial": str(data.get("serial") or ""),
            "credential": str(data.get("credential") or ""),
            "device_id": str(data.get("deviceId") or data.get("device_id") or ""),
            "credential_prefix": str(
                data.get("credentialPrefix") or data.get("credential_prefix") or ""
            ),
            "activated_at": str(data.get("activatedAt") or data.get("activated_at") or ""),
            "stock_status": str(data.get("stockStatus") or data.get("stock_status") or ""),
            "binding_status": str(
                data.get("bindingStatus") or data.get("binding_status") or ""
            ),
            "customer_bound": bool(data.get("customerBound", data.get("customer_bound", False))),
        }
        extra = {k: v for k, v in data.items() if k not in (
            "serial", "credential", "deviceId", "device_id",
            "credentialPrefix", "credential_prefix",
            "activatedAt", "activated_at",
            "stockStatus", "stock_status",
            "bindingStatus", "binding_status",
            "customerBound", "customer_bound",
        )}
        if not mapped["credential"]:
            raise ValueError("activate response missing credential")
        if not mapped["serial"]:
            raise ValueError("activate response missing serial")
        if not mapped["credential_prefix"] and mapped["credential"]:
            mapped["credential_prefix"] = mapped["credential"][:16]
        mapped["extra"] = extra or None
        return cls(**mapped)

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
        }


class CredentialStore:
    """JSON credential file under the service state directory."""

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
            extra=raw.get("extra") if isinstance(raw.get("extra"), dict) else None,
        )

    def save(self, creds: DeviceCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "serial": creds.serial,
            "credential": creds.credential,
            "deviceId": creds.device_id,
            "credentialPrefix": creds.credential_prefix,
            "activatedAt": creds.activated_at,
            "stockStatus": creds.stock_status,
            "bindingStatus": creds.binding_status,
            "customerBound": creds.customer_bound,
        }
        if creds.extra:
            payload["extra"] = creds.extra
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

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
