"""MoisHome open-platform integration layer (device-api).

Keeps cloud activate / heartbeat / shadow / catalog / entitlement calls out of
the offline inference core. See ``docs/moishome/README.md``.
"""

from __future__ import annotations

from .client import DeviceApiClient, DeviceApiError
from .config import OpenPlatformConfig
from .credentials import CredentialStore, DeviceCredentials

__all__ = [
    "CredentialStore",
    "DeviceApiClient",
    "DeviceApiError",
    "DeviceCredentials",
    "OpenPlatformConfig",
]
