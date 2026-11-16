"""Unit tests for the MoisHome device-api client (fixture / MockTransport only)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.moishome import (
    DeviceApiClient,
    MoishomeApiError,
    MoishomeConfig,
    load_config,
)
from app.moishome.client import CLOUD_DEVICE_CATALOG_PATH, LOCAL_BOARD_CATALOG_PATH
from app.moishome.credentials import CredentialsStore, DeviceCredentials

FIXTURES = Path(__file__).parent / "fixtures" / "moishome"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _envelope_transport(routes: dict[tuple[str, str], dict]) -> httpx.MockTransport:
    """Map (METHOD, path-suffix) → envelope JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # strip /api/v1 prefix if present
        if path.startswith("/api/v1"):
            path = path[len("/api/v1") :]
        key = (request.method.upper(), path)
        if key not in routes:
            return httpx.Response(
                404,
                json={
                    "ok": False,
                    "error": {"code": "NOT_FOUND", "message": f"no fixture for {key}"},
                    "requestId": "test",
                },
            )
        body = routes[key]
        status = 200
        if body.get("ok") is False:
            code = (body.get("error") or {}).get("code", "")
            status = {
                "VALIDATION_FAILED": 422,
                "KEY_INVALID": 401,
                "RATE_LIMITED": 429,
                "ACTIVATION_BACKEND_PENDING": 501,
                "ENTITLEMENT_REQUIRED": 403,
            }.get(code, 400)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_load_config_and_api_root():
    cfg = load_config(
        {
            "MOISHOME_BASE_URL": "https://platform.example.invalid/",
            "MOISHOME_SERIAL": "SN-1",
            "MOISHOME_TEMP_KEY": "fixture-temp-key-16",
            "MOISHOME_SKU": "64G",
        }
    )
    assert cfg.api_root == "https://platform.example.invalid/api/v1"
    assert cfg.enabled()
    assert cfg.serial == "SN-1"


def test_credentials_atomic_persist(tmp_path: Path):
    store = CredentialsStore(tmp_path)
    creds = DeviceCredentials.from_activate_data(_load_fixture("activate_success.json")["data"])
    store.save(creds)
    loaded = store.load()
    assert loaded is not None
    assert loaded.serial == "SN-ZETAO-FIXTURE-001"
    assert loaded.credential.startswith("mh_dev_")
    assert "credential" not in loaded.public()
    store.update_config_version(3)
    assert store.load().config_version == 3


def test_activate_persists_credential(tmp_path: Path):
    activate = _load_fixture("activate_success.json")
    transport = _envelope_transport({("POST", "/device-api/activate"): activate})
    cfg = MoishomeConfig(
        base_url="https://platform.example.invalid",
        serial="SN-ZETAO-FIXTURE-001",
        temp_key="fixture-temp-key-16",
        state_dir=tmp_path,
    )
    with DeviceApiClient(cfg, transport=transport) as client:
        creds = client.activate()
        assert creds.device_id == "dev_fixture_001"
        assert client.is_activated()
    # reload from disk
    with DeviceApiClient(cfg, transport=transport) as client2:
        assert client2.is_activated()
        assert client2.credentials.credential == activate["data"]["credential"]


def test_activate_validation_short_temp_key(tmp_path: Path):
    cfg = MoishomeConfig(
        base_url="https://platform.example.invalid",
        state_dir=tmp_path,
    )
    with DeviceApiClient(cfg, transport=_envelope_transport({})) as client:
        with pytest.raises(MoishomeApiError) as ei:
            client.activate("SN-1", "short")
        assert ei.value.code == "VALIDATION_FAILED"


def test_heartbeat_shadow_and_acks(tmp_path: Path):
    activate = _load_fixture("activate_success.json")
    heartbeat = _load_fixture("heartbeat_success.json")
    shadow_body = {
        "ok": True,
        "data": {
            "device": {"id": "dev_fixture_001", "serial": "SN-ZETAO-FIXTURE-001"},
            "shadow": heartbeat["data"]["shadow"],
            "config": heartbeat["data"]["config"],
            "pendingOta": heartbeat["data"]["pendingOta"],
            "compat": heartbeat["data"]["compat"],
        },
        "requestId": "req_shadow",
    }
    ota_ack = {"ok": True, "data": {"ok": True, "taskStatus": "running"}, "requestId": "req_ota"}
    cfg_ack = {"ok": True, "data": {"ok": True}, "requestId": "req_cfg"}

    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/v1"):
            path = path[len("/api/v1") :]
        body = None
        if request.content:
            body = json.loads(request.content.decode())
        seen.append((request.method, path, body))
        auth = request.headers.get("authorization", "")
        serial_hdr = request.headers.get("x-device-serial", "")
        table = {
            ("POST", "/device-api/activate"): activate,
            ("POST", "/device-api/heartbeat"): heartbeat,
            ("GET", "/device-api/shadow"): shadow_body,
            ("POST", "/device-api/ota/ack"): ota_ack,
            ("POST", "/device-api/config/ack"): cfg_ack,
        }
        payload = table[(request.method, path)]
        if path != "/device-api/activate":
            assert auth.startswith("Bearer mh_dev_")
            assert serial_hdr == "SN-ZETAO-FIXTURE-001"
        return httpx.Response(200, json=payload)

    cfg = MoishomeConfig(
        base_url="https://platform.example.invalid",
        serial="SN-ZETAO-FIXTURE-001",
        temp_key="fixture-temp-key-16",
        firmware="0.6.37",
        sku="64G",
        state_dir=tmp_path,
    )
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        beat = client.heartbeat()
        assert beat["pendingOta"]["jobId"] == "ota_job_fixture"
        assert beat["config"]["version"] == 3
        sh = client.shadow()
        assert sh["shadow"]["reported"]["firmware"] == "0.6.37"
        client.ack_ota("ota_job_fixture", "downloading")
        client.ack_config(3)
        assert client.credentials.config_version == 3

    methods = {(m, p) for m, p, _ in seen}
    assert ("POST", "/device-api/heartbeat") in methods
    assert ("POST", "/device-api/ota/ack") in methods
    assert ("POST", "/device-api/config/ack") in methods
    hb_body = next(b for m, p, b in seen if m == "POST" and p == "/device-api/heartbeat")
    assert hb_body["firmware"] == "0.6.37"
    assert hb_body["attributes"]["sku"] == "64G"


def test_cloud_catalog_and_entitlement_not_local_api_catalog(tmp_path: Path):
    activate = _load_fixture("activate_success.json")
    catalog = _load_fixture("catalog_success.json")
    allowed = _load_fixture("entitlement_allowed.json")
    required = _load_fixture("entitlement_required.json")
    hit_local = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hit_local
        path = request.url.path
        if path == LOCAL_BOARD_CATALOG_PATH or path.endswith(LOCAL_BOARD_CATALOG_PATH):
            hit_local = True
            return httpx.Response(500, json={"detail": "must not call local catalog"})
        if path.startswith("/api/v1"):
            path = path[len("/api/v1") :]
        table = {
            ("POST", "/device-api/activate"): activate,
            ("GET", CLOUD_DEVICE_CATALOG_PATH): catalog,
            ("POST", "/device-api/entitlement/check"): allowed,
        }
        # second entitlement call returns required
        if request.method == "POST" and path == "/device-api/entitlement/check":
            body = json.loads(request.content.decode()) if request.content else {}
            if body.get("contentType") == "plugin":
                return httpx.Response(200, json=required)
        return httpx.Response(200, json=table[(request.method, path)])

    assert CLOUD_DEVICE_CATALOG_PATH != LOCAL_BOARD_CATALOG_PATH
    assert LOCAL_BOARD_CATALOG_PATH == "/api/catalog"

    cfg = MoishomeConfig(
        base_url="https://platform.example.invalid",
        serial="SN-ZETAO-FIXTURE-001",
        temp_key="fixture-temp-key-16",
        state_dir=tmp_path,
    )
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        cat = client.cloud_catalog(types="products")
        assert cat["products"][0]["id"] == "pkg_fixture"
        ok = client.entitlement_check(capability="skill:foo")
        assert ok["allowed"] is True
        denied = client.entitlement_check(content_type="plugin")
        assert denied["allowed"] is False
        assert denied["code"] == "ENTITLEMENT_REQUIRED"
    assert not hit_local


def test_platform_error_envelope(tmp_path: Path):
    transport = _envelope_transport(
        {
            ("POST", "/device-api/activate"): {
                "ok": False,
                "error": {"code": "KEY_INVALID", "message": "激活失败"},
                "requestId": "r1",
            }
        }
    )
    cfg = MoishomeConfig(
        base_url="https://platform.example.invalid",
        state_dir=tmp_path,
    )
    with DeviceApiClient(cfg, transport=transport) as client:
        with pytest.raises(MoishomeApiError) as ei:
            client.activate("SN-ZETAO-FIXTURE-001", "fixture-temp-key-16")
        assert ei.value.code == "KEY_INVALID"
        assert ei.value.status == 401
        assert ei.value.request_id == "r1"


def test_fixture_env_example_mentions_gate():
    text = (FIXTURES / "platform.env.example").read_text(encoding="utf-8")
    assert "MOISHOME_BASE_URL=" in text
    assert "MOISHOME_SERIAL=" in text
    assert "MOISHOME_TEMP_KEY=" in text
    assert "CATALOG_ENTITLEMENT_GATE=0" in text
