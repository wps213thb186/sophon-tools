"""Environment-backed settings for the MoisHome device-api client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MoishomeConfig:
    """Platform connection settings.

    ``base_url`` is the platform root without ``/api/v1`` (the client appends it).
    ``CATALOG_ENTITLEMENT_GATE`` is a *platform* process env flag — not read here.
    """

    base_url: str
    serial: str = ""
    temp_key: str = ""
    firmware: str = ""
    config_version: int = 0
    sku: str = ""
    timeout_s: float = 15.0
    state_dir: Path | None = None

    @property
    def api_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1"

    def enabled(self) -> bool:
        return bool(self.base_url.strip())


def load_config(
    env: dict[str, str] | None = None,
    *,
    state_dir: Path | None = None,
) -> MoishomeConfig:
    """Load from ``os.environ`` (or a substitute mapping)."""
    e = env if env is not None else os.environ
    timeout_raw = (e.get("MOISHOME_HTTP_TIMEOUT_S") or "15").strip()
    try:
        timeout_s = float(timeout_raw)
    except ValueError:
        timeout_s = 15.0
    cfg_ver_raw = (e.get("MOISHOME_CONFIG_VERSION") or "0").strip()
    try:
        config_version = int(cfg_ver_raw)
    except ValueError:
        config_version = 0
    return MoishomeConfig(
        base_url=(e.get("MOISHOME_BASE_URL") or "").strip(),
        serial=(e.get("MOISHOME_SERIAL") or "").strip(),
        temp_key=(e.get("MOISHOME_TEMP_KEY") or "").strip(),
        firmware=(e.get("MOISHOME_FIRMWARE") or "").strip(),
        config_version=config_version,
        sku=(e.get("MOISHOME_SKU") or "").strip(),
        timeout_s=timeout_s,
        state_dir=state_dir,
    )
