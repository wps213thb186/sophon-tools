"""Persist device credential after SN + tempKey activation."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FILENAME = "moishome_device.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class DeviceCredentials:
    serial: str
    device_id: str = ""
    credential: str = ""
    credential_prefix: str = ""
    activated_at: str = ""
    stock_status: str = ""
    binding_status: str = ""
    customer_bound: bool = False
    config_version: int = 0
    updated_at: str = ""

    def public(self) -> dict[str, Any]:
        """Safe for logs / UI: never includes the raw credential."""
        d = asdict(self)
        d.pop("credential", None)
        return d

    @classmethod
    def from_activate_data(cls, data: dict[str, Any], *, config_version: int = 0) -> DeviceCredentials:
        return cls(
            serial=str(data.get("serial") or ""),
            device_id=str(data.get("deviceId") or ""),
            credential=str(data.get("credential") or ""),
            credential_prefix=str(data.get("credentialPrefix") or ""),
            activated_at=str(data.get("activatedAt") or ""),
            stock_status=str(data.get("stockStatus") or ""),
            binding_status=str(data.get("bindingStatus") or ""),
            customer_bound=bool(data.get("customerBound")),
            config_version=config_version,
            updated_at=_utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeviceCredentials:
        return cls(
            serial=str(raw.get("serial") or ""),
            device_id=str(raw.get("deviceId") or ""),
            credential=str(raw.get("credential") or ""),
            credential_prefix=str(raw.get("credentialPrefix") or ""),
            activated_at=str(raw.get("activatedAt") or ""),
            stock_status=str(raw.get("stockStatus") or ""),
            binding_status=str(raw.get("bindingStatus") or ""),
            customer_bound=bool(raw.get("customerBound")),
            config_version=int(raw.get("configVersion") or 0),
            updated_at=str(raw.get("updatedAt") or ""),
        )

    def activated(self) -> bool:
        return bool(self.serial and self.credential)


class CredentialsStore:
    """Atomic JSON file under the service ``state/`` directory."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / FILENAME
        self._lock = threading.Lock()

    def load(self) -> DeviceCredentials | None:
        with self._lock:
            if not self.path.exists():
                return None
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if not isinstance(raw, dict):
                return None
            creds = DeviceCredentials.from_dict(raw)
            return creds if creds.activated() else None

    def save(self, creds: DeviceCredentials) -> None:
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            payload = creds.to_dict()
            payload["updatedAt"] = _utc_now()
            tmp = self.path.with_name(FILENAME + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)

    def update_config_version(self, version: int) -> DeviceCredentials | None:
        with self._lock:
            creds = None
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        creds = DeviceCredentials.from_dict(raw)
                except (OSError, json.JSONDecodeError):
                    creds = None
            if creds is None or not creds.activated():
                return None
            creds.config_version = int(version)
            creds.updated_at = _utc_now()
            payload = creds.to_dict()
            tmp = self.path.with_name(FILENAME + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            return creds

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
