"""Environment-backed settings for the MoisHome device-api client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _env_path(name: str) -> Path | None:
    raw = _env(name)
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class OpenPlatformConfig:
    """Connection and identity knobs for the open platform.

    ``base_url`` must include the API prefix (``…/api/v1``). Paths below are
    relative to that root (``/device-api/…``).
    """

    base_url: str
    serial: str = ""
    temp_key: str = ""
    sku: str = ""
    model: str = "CV84X6"
    firmware: str = "0.0.0"
    config_version: int = 0
    credentials_path: Path | None = None
    identity_file: Path | None = None
    heartbeat_interval_s: float = 60.0
    timeout_s: float = 30.0

    @classmethod
    def from_env(cls, data_dir: Path | None = None) -> "OpenPlatformConfig":
        creds = _env_path("MOISHOME_CREDENTIALS_PATH")
        if creds is None and data_dir is not None:
            creds = Path(data_dir) / "state" / "moishome" / "credentials.json"
        return cls(
            base_url=_env("MOISHOME_BASE_URL").rstrip("/"),
            serial=_env("MOISHOME_SERIAL"),
            temp_key=_env("MOISHOME_TEMP_KEY"),
            sku=_env("MOISHOME_SKU"),
            model=_env("MOISHOME_MODEL", "CV84X6"),
            firmware=_env("MOISHOME_FIRMWARE", "0.0.0"),
            config_version=int(_env("MOISHOME_CONFIG_VERSION", "0") or "0"),
            credentials_path=creds,
            identity_file=_env_path("MOISHOME_IDENTITY_FILE"),
            heartbeat_interval_s=_env_float("MOISHOME_HEARTBEAT_INTERVAL_S", 60.0),
            timeout_s=_env_float("MOISHOME_TIMEOUT_S", 30.0),
        )

    def require_base_url(self) -> str:
        if not self.base_url:
            raise ValueError("MOISHOME_BASE_URL is required (include /api/v1)")
        return self.base_url
