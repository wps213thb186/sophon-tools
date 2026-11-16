"""Device serial / SKU / model resolution for open-platform calls."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import OpenPlatformConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceIdentity:
    serial: str
    sku: str = ""
    model: str = "CV84X6"


def _from_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("identity file %s unreadable: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _first_nonempty(*values: str) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def resolve_identity(cfg: OpenPlatformConfig) -> DeviceIdentity:
    """Resolve SN / SKU / model: env fields → identity JSON → defaults."""

    file_data: dict = {}
    if cfg.identity_file is not None:
        file_data = _from_file(cfg.identity_file)

    serial = _first_nonempty(cfg.serial, str(file_data.get("serial") or ""))
    sku = _first_nonempty(cfg.sku, str(file_data.get("sku") or ""))
    model = _first_nonempty(cfg.model, str(file_data.get("model") or ""), "CV84X6")
    return DeviceIdentity(serial=serial, sku=sku, model=model)


def require_serial(identity: DeviceIdentity) -> str:
    if not identity.serial:
        raise ValueError("device serial missing (set MOISHOME_SERIAL or identity file)")
    return identity.serial
