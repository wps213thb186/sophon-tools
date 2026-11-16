"""Environment-backed settings for the MoisHome device-api client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

API_PREFIX = "/api/v1"
CREDENTIALS_FILENAME = "moishome_device.json"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def default_credentials_path(state_dir: Path | None = None, data_dir: Path | None = None) -> Path:
    """``<data_dir>/state/moishome_device.json`` (or ``<state_dir>/moishome_device.json``)."""
    override = _env("MOISHOME_CREDENTIALS_PATH")
    if override:
        return Path(override).expanduser()
    if state_dir is not None:
        return Path(state_dir) / CREDENTIALS_FILENAME
    root = Path(data_dir) if data_dir is not None else Path(_env("CV84X6_DATA_DIR") or ".")
    return root / "state" / CREDENTIALS_FILENAME


def api_base(platform_root: str) -> str:
    """Join platform root (no ``/api/v1``) with the API prefix."""
    root = platform_root.rstrip("/")
    if not root:
        raise ValueError("MOISHOME_BASE_URL is required (platform root, without /api/v1)")
    if root.endswith(API_PREFIX):
        # Tolerate accidental inclusion; canonical is without the prefix.
        return root
    return f"{root}{API_PREFIX}"


@dataclass(frozen=True)
class OpenPlatformConfig:
    """Connection and identity knobs for the open platform.

    ``base_url`` is the **platform root** (no ``/api/v1``). The HTTP layer
    appends the prefix.
    """

    base_url: str
    serial: str = ""
    temp_key: str = ""
    sku: str = ""
    model: str = ""
    firmware: str = ""
    config_version: int = 0
    credentials_path: Path | None = None
    identity_file: Path | None = None
    heartbeat_interval_s: float = 60.0
    timeout_s: float = 15.0
    heartbeat_enabled: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        state_dir: Path | None = None,
        data_dir: Path | None = None,
    ) -> "OpenPlatformConfig":
        firmware = _env("MOISHOME_FIRMWARE")
        if not firmware:
            try:
                from .. import __version__ as pkg_version
            except Exception:  # pragma: no cover - package always present in-tree
                pkg_version = "0.0.0"
            firmware = str(pkg_version)

        identity_raw = _env("MOISHOME_IDENTITY_FILE")
        base_url = _env("MOISHOME_BASE_URL").rstrip("/")
        # Background heartbeat only when explicitly opted in (never surprise a
        # board that merely has a URL in the env file).
        heartbeat = _env_truthy("MOISHOME_HEARTBEAT", False)
        return cls(
            base_url=base_url,
            serial=_env("MOISHOME_SERIAL"),
            temp_key=_env("MOISHOME_TEMP_KEY"),
            sku=_env("MOISHOME_SKU"),
            model=_env("MOISHOME_MODEL"),
            firmware=firmware,
            config_version=int(_env("MOISHOME_CONFIG_VERSION", "0") or "0"),
            credentials_path=default_credentials_path(state_dir=state_dir, data_dir=data_dir),
            identity_file=Path(identity_raw).expanduser() if identity_raw else None,
            heartbeat_interval_s=_env_float("MOISHOME_HEARTBEAT_INTERVAL_S", 60.0),
            # Prefer design name; keep TIMEOUT_S as alias for earlier draft.
            timeout_s=_env_float(
                "MOISHOME_HTTP_TIMEOUT_S",
                _env_float("MOISHOME_TIMEOUT_S", 15.0),
            ),
            heartbeat_enabled=heartbeat,
        )

    @property
    def enabled(self) -> bool:
        """True when a platform root URL is configured (cloud path may run)."""
        return bool(self.base_url)

    def api_root(self) -> str:
        return api_base(self.base_url)
