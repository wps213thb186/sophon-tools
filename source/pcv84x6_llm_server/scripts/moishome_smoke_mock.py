#!/usr/bin/env python3
"""Offline smoke of the MoisHome device-api client using fixture envelopes.

Proves activate → heartbeat → shadow → cloud_catalog → entitlement_check →
OTA/config ACK without a real platform URL or factory secrets.

Real-device replacement points (edit env, not this script):
  MOISHOME_BASE_URL   platform root WITHOUT /api/v1
  MOISHOME_SERIAL     factory SN
  MOISHOME_TEMP_KEY   factory temp key (≥16)
  MOISHOME_SKU        optional attributes.sku

Then run:  python -m app.moishome.cli smoke
Or hit:    /moishome/* on a running service (admin auth).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.moishome import DeviceApiClient, OpenPlatformConfig  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "moishome"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _envelope(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "data": data, "requestId": "mock_smoke"})


def main() -> int:
    activate = _load("activate_success.json")
    heartbeat = _load("heartbeat_success.json")
    catalog = _load("catalog_success.json")
    entitlement = _load("entitlement_required.json")
    steps: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/device-api/activate"):
            return _envelope(activate)
        if path.endswith("/device-api/heartbeat"):
            return _envelope(heartbeat)
        if path.endswith("/device-api/shadow"):
            return _envelope(
                {
                    "shadow": heartbeat.get("shadow"),
                    "config": heartbeat.get("config"),
                    "pendingOta": heartbeat.get("pendingOta"),
                }
            )
        if path.endswith("/device-api/catalog"):
            return _envelope(catalog)
        if path.endswith("/device-api/entitlement/check"):
            return _envelope(entitlement)
        if path.endswith("/device-api/ota/ack"):
            return _envelope({"acked": True})
        if path.endswith("/device-api/config/ack"):
            return _envelope({"acked": True})
        return httpx.Response(
            404, json={"ok": False, "error": {"code": "NOT_FOUND", "message": path}}
        )

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        cfg = OpenPlatformConfig(
            base_url="https://mock.moishome.invalid",
            serial="SN-FACTORY-001",
            temp_key="fixture-temp-key16",
            sku="64G",
            model="CV84X6",
            firmware="0.6.37",
            config_version=3,
            credentials_path=state / "moishome_device.json",
            timeout_s=5.0,
        )
        with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
            creds = client.activate()
            steps.append(f"activate ok prefix={creds.credential_prefix}")
            assert (state / "moishome_device.json").exists()
            hb = client.heartbeat()
            steps.append(f"heartbeat online={hb.get('online')}")
            sh = client.shadow()
            steps.append(f"shadow keys={sorted(sh.keys())}")
            cat = client.cloud_catalog(types="products,skills")
            steps.append(f"cloud_catalog products={len(cat.get('products') or [])}")
            ent = client.entitlement_check(content_type="skill", capability="skill:smoke")
            steps.append(f"entitlement_check allowed={ent.get('allowed')} code={ent.get('code')}")
            job = (hb.get("pendingOta") or {}).get("jobId")
            if job:
                client.ack_ota(job, "applied")
                steps.append(f"ota_ack jobId={job}")
            ver = (hb.get("config") or {}).get("version")
            if ver is not None:
                client.ack_config(int(ver))
                steps.append(f"config_ack version={ver} local={client.config_version}")

    print("MOISHOME_MOCK_SMOKE_OK")
    for line in steps:
        print(f"  - {line}")
    print(
        "NOTE: this used MockTransport + fixtures only; "
        "it does NOT prove reachability of a real open platform."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
