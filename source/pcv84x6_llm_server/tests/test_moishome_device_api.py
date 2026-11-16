"""Unit tests for the MoisHome device-api integration layer (mock transport)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.moishome.client import DeviceApiClient, DeviceApiError
from app.moishome.config import OpenPlatformConfig
from app.moishome.credentials import CredentialStore, DeviceCredentials
from app.moishome.identity import resolve_identity
from app.moishome.runtime import activate_and_persist, run_cycle


def _cfg(**kwargs) -> OpenPlatformConfig:
    base = dict(
        base_url="https://platform.test/api/v1",
        serial="SN-TEST-001",
        temp_key="temp-key-16chars!",
        sku="64G",
        model="CV84X6",
        firmware="1.2.0",
        config_version=3,
        credentials_path=None,
        heartbeat_interval_s=60.0,
        timeout_s=5.0,
    )
    base.update(kwargs)
    return OpenPlatformConfig(**base)


def _envelope(data, request_id="req_1"):
    return httpx.Response(200, json={"ok": True, "data": data, "requestId": request_id})


def test_resolve_identity_prefers_env(tmp_path: Path):
    ident = tmp_path / "id.json"
    ident.write_text(json.dumps({"serial": "FROM-FILE", "sku": "32G", "model": "OTHER"}))
    cfg = _cfg(serial="FROM-ENV", sku="64G", identity_file=ident)
    got = resolve_identity(cfg)
    assert got.serial == "FROM-ENV"
    assert got.sku == "64G"


def test_credential_store_roundtrip(tmp_path: Path):
    path = tmp_path / "creds.json"
    store = CredentialStore(path)
    creds = DeviceCredentials.from_activate_data(
        {
            "deviceId": "dev-1",
            "serial": "SN-1",
            "activatedAt": "2026-01-01T00:00:00Z",
            "credential": "mh_dev_secret_value",
            "credentialPrefix": "mh_dev_secre",
            "stockStatus": "outbound",
            "bindingStatus": "unbound",
            "customerBound": False,
        }
    )
    store.save(creds)
    loaded = store.load()
    assert loaded is not None
    assert loaded.credential == "mh_dev_secret_value"
    assert loaded.device_id == "dev-1"
    assert "mh_dev_secret_value" not in json.dumps(loaded.public_dict())


def test_activate_persists(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/device-api/activate")
        body = json.loads(request.content)
        assert body["serial"] == "SN-TEST-001"
        assert body["tempKey"] == "temp-key-16chars!"
        assert "Authorization" not in request.headers
        return _envelope(
            {
                "deviceId": "d1",
                "serial": "SN-TEST-001",
                "activatedAt": "2026-11-16T00:00:00Z",
                "credential": "mh_dev_formal",
                "credentialPrefix": "mh_dev_form",
                "stockStatus": "outbound",
                "bindingStatus": "unbound",
                "customerBound": False,
            }
        )

    transport = httpx.MockTransport(handler)
    cfg = _cfg(credentials_path=tmp_path / "c.json")
    store = CredentialStore(cfg.credentials_path)
    with DeviceApiClient(cfg, transport=transport) as client:
        creds = activate_and_persist(
            client, store, serial=cfg.serial, temp_key=cfg.temp_key
        )
    assert creds.credential == "mh_dev_formal"
    assert store.load().credential == "mh_dev_formal"


def test_heartbeat_sends_auth_and_sku():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["serial"] = request.headers.get("x-device-serial")
        seen["body"] = json.loads(request.content)
        return _envelope({"online": True, "config": {"version": 4}, "pendingOta": None})

    cfg = _cfg()
    with DeviceApiClient(
        cfg, credential="mh_dev_x", serial="SN-TEST-001", transport=httpx.MockTransport(handler)
    ) as client:
        data = client.heartbeat(attributes={"sku": "64G"})
    assert seen["auth"] == "Bearer mh_dev_x"
    assert seen["serial"] == "SN-TEST-001"
    assert seen["body"]["firmware"] == "1.2.0"
    assert seen["body"]["configVersion"] == 3
    assert seen["body"]["attributes"]["sku"] == "64G"
    assert data["online"] is True


def test_envelope_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "ok": False,
                "error": {"code": "KEY_INVALID", "message": "invalid"},
                "requestId": "r9",
            },
        )

    cfg = _cfg()
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DeviceApiError) as ei:
            client.activate("SN-X", "temp-key-16chars!")
    assert ei.value.code == "KEY_INVALID"
    assert ei.value.request_id == "r9"
    assert ei.value.status == 401


def test_catalog_and_entitlement_paths():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/catalog"):
            return _envelope({"products": [], "models": [], "compat": {"applied": False}})
        return _envelope({"allowed": False, "code": "ENTITLEMENT_REQUIRED"})

    cfg = _cfg()
    with DeviceApiClient(
        cfg, credential="mh_dev_x", serial="SN-1", transport=httpx.MockTransport(handler)
    ) as client:
        cat = client.get_catalog(model="CV84X6", sku="64G")
        ent = client.check_entitlement(content_type="skill", capability="skill:smoke")
    assert paths[0].endswith("/device-api/catalog")
    assert paths[1].endswith("/device-api/entitlement/check")
    assert "products" in cat
    assert ent["allowed"] is False


def test_run_cycle_acks_ota_and_config():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/heartbeat"):
            return _envelope(
                {
                    "online": True,
                    "pendingOta": {"jobId": "job-9", "version": "2.0"},
                    "config": {"version": 7},
                }
            )
        if request.url.path.endswith("/shadow"):
            return _envelope({"shadow": {"desired": {}, "reported": {}}})
        if request.url.path.endswith("/ota/ack"):
            body = json.loads(request.content)
            assert body["jobId"] == "job-9"
            assert body["status"] == "applied"
            return _envelope({"acked": True})
        if request.url.path.endswith("/config/ack"):
            body = json.loads(request.content)
            assert body["version"] == 7
            return _envelope({"acked": True})
        return httpx.Response(404, json={"ok": False, "error": {"code": "NOT_FOUND"}})

    from app.moishome.identity import DeviceIdentity

    cfg = _cfg()
    identity = DeviceIdentity(serial="SN-TEST-001", sku="64G", model="CV84X6")
    with DeviceApiClient(
        cfg, credential="mh_dev_x", serial="SN-TEST-001", transport=httpx.MockTransport(handler)
    ) as client:
        out = run_cycle(client, cfg, identity, auto_ack=True)
    assert out.errors == []
    assert out.ota_ack["acked"] is True
    assert out.config_ack["acked"] is True
    assert any(p.endswith("/ota/ack") for _, p in calls)
    assert any(p.endswith("/config/ack") for _, p in calls)


def test_cli_status_help():
    from app.moishome.cli import main

    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_activate_rejects_short_temp_key():
    cfg = _cfg()
    with DeviceApiClient(cfg, transport=httpx.MockTransport(lambda r: _envelope({}))) as client:
        with pytest.raises(DeviceApiError) as ei:
            client.activate("SN-1", "short")
    assert ei.value.code == "VALIDATION_FAILED"
