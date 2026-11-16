"""MoisHome open-platform integration layer (device-api).

Optional library — does not alter offline ``create_app`` behaviour.
Cloud catalog is ``DeviceApiClient.cloud_catalog``; never confuse with local
``GET /api/catalog``. See ``docs/moishome/README.md``.
"""

from __future__ import annotations

from .client import DeviceApiClient
from .config import OpenPlatformConfig
from .credentials import CredentialStore, DeviceCredentials
from .http import DeviceApiError, MoishomeApiError
from .service import MoishomeBridge

__all__ = [
    "CredentialStore",
    "DeviceApiClient",
    "DeviceApiError",
    "DeviceCredentials",
    "MoishomeApiError",
    "MoishomeBridge",
    "OpenPlatformConfig",
]
