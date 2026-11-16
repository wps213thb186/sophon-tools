"""Unit tests for the MoisHome device-api integration layer (mock transport)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from app.moishome import DeviceApiClient, MoishomeApiError, OpenPlatformConfig
from app.moishome.config import api_base, default_credentials_path
from app.moishome.credentials import CredentialStore, DeviceCredentials
from app.moishome.http import unpack_envelope
from app.moishome.identity import resolve_identity
from app.moishome.runtime import run_cycle

FIXTURES = Path(__file__).parent / "fixtures" / "moishome"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _cfg(tmp_path: Path | None = None, **kwargs) -> OpenPlatformConfig:
    base = dict(
        base_url="https://platform.test",
        serial="SN-TEST-001",
        temp_key="temp-key-16chars!",
        sku="64G",
        model="CV84X6",
        firmware="1.2.0",
        config_version=3,
        credentials_path=(tmp_path / "state" / "moishome_device.json") if tmp_path else None,
        heartbeat_interval_s=60.0,
        timeout_s=5.0,
    )
    base.update(kwargs)
    return OpenPlatformConfig(**base)


def _envelope(data, request_id="req_1", status=200):
    return httpx.Response(status, json={"ok": True, "data": data, "requestId": request_id})


def test_api_base_appends_prefix():
    assert api_base("https://host.example") == "https://host.example/api/v1"
    assert api_base("https://host.example/") == "https://host.example/api/v1"
    # Tolerates accidental inclusion
    assert api_base("https://host.example/api/v1") == "https://host.example/api/v1"


def test_default_credentials_path(tmp_path: Path):
    assert default_credentials_path(state_dir=tmp_path) == tmp_path / "moishome_device.json"
    assert (
        default_credentials_path(data_dir=tmp_path)
        == tmp_path / "state" / "moishome_device.json"
    )


def test_resolve_identity_prefers_env(tmp_path: Path):
    ident = tmp_path / "id.json"
    ident.write_text(json.dumps({"serial": "FROM-FILE", "sku": "32G", "model": "OTHER"}))
    cfg = _cfg(serial="FROM-ENV", sku="64G", identity_file=ident)
    got = resolve_identity(cfg)
    assert got.serial == "FROM-ENV"
    assert got.sku == "64G"


def test_credential_store_roundtrip_matches_design_schema(tmp_path: Path):
    path = tmp_path / "state" / "moishome_device.json"
    store = CredentialStore(path)
    creds = DeviceCredentials.from_activate_data(_load_fixture("activate_success.json"))
    store.save(creds)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "serial",
        "deviceId",
        "credential",
        "credentialPrefix",
        "activatedAt",
        "stockStatus",
        "bindingStatus",
        "customerBound",
        "configVersion",
        "updatedAt",
    ):
        assert key in raw
    loaded = store.load()
    assert loaded is not None
    assert loaded.credential.startswith("mh_dev_")
    assert "mh_dev_fixture_formal_credential" not in json.dumps(loaded.public_dict())


def test_unpack_envelope_error():
    resp = httpx.Response(
        401,
        json={
            "ok": False,
            "error": {"code": "KEY_INVALID", "message": "invalid"},
            "requestId": "r9",
        },
    )
    with pytest.raises(MoishomeApiError) as ei:
        unpack_envelope(resp)
    assert ei.value.code == "KEY_INVALID"
    assert ei.value.request_id == "r9"
    assert ei.value.status == 401


def test_activate_persists(tmp_path: Path):
    activate_data = _load_fixture("activate_success.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://platform.test/api/v1/")
        assert request.url.path.endswith("/device-api/activate")
        body = json.loads(request.content)
        assert body["serial"] == "SN-TEST-001"
        assert body["tempKey"] == "temp-key-16chars!"
        assert "Authorization" not in request.headers
        return _envelope(activate_data)

    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        creds = client.activate()
    assert creds.credential.startswith("mh_dev_")
    assert (tmp_path / "state" / "moishome_device.json").exists()
    loaded = CredentialStore(cfg.credentials_path).load()
    assert loaded is not None
    assert loaded.credential == activate_data["credential"]


def test_from_env_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOISHOME_BASE_URL", "https://env.example")
    monkeypatch.setenv("MOISHOME_SERIAL", "SN-ENV")
    monkeypatch.setenv("MOISHOME_TEMP_KEY", "temp-key-16chars!")
    monkeypatch.setenv("MOISHOME_SKU", "64G")
    monkeypatch.delenv("MOISHOME_CREDENTIALS_PATH", raising=False)

    activate_data = _load_fixture("activate_success.json")
    activate_data = {**activate_data, "serial": "SN-ENV"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/device-api/activate")
        return _envelope(activate_data)

    with DeviceApiClient.from_env(
        state_dir=tmp_path, transport=httpx.MockTransport(handler)
    ) as client:
        assert client.api_root == "https://env.example/api/v1"
        creds = client.activate()
        assert creds.serial == "SN-ENV"
        assert (tmp_path / "moishome_device.json").exists()


def test_heartbeat_sends_auth_and_sku(tmp_path: Path):
    seen = {}
    hb = _load_fixture("heartbeat_success.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activate"):
            return _envelope(_load_fixture("activate_success.json"))
        seen["auth"] = request.headers.get("Authorization")
        seen["serial"] = request.headers.get("x-device-serial")
        seen["body"] = json.loads(request.content)
        return _envelope(hb)

    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        data = client.heartbeat()
    assert seen["auth"].startswith("Bearer mh_dev_")
    assert seen["serial"] == "SN-FACTORY-001" or seen["serial"] == "SN-TEST-001"
    assert seen["body"]["firmware"] == "1.2.0"
    assert seen["body"]["configVersion"] == 3
    assert seen["body"]["attributes"]["sku"] == "64G"
    assert data["online"] is True
    assert data["pendingOta"]["jobId"] == "ota_job_fixture_1"


def test_shadow_cloud_catalog_entitlement(tmp_path: Path):
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/activate"):
            return _envelope(_load_fixture("activate_success.json"))
        if request.url.path.endswith("/shadow"):
            return _envelope(
                {
                    "device": {"serial": "SN-FACTORY-001"},
                    "shadow": {"desired": {}, "reported": {}},
                    "config": {"version": 1},
                    "pendingOta": None,
                }
            )
        if request.url.path.endswith("/catalog"):
            return _envelope(_load_fixture("catalog_success.json"))
        if request.url.path.endswith("/entitlement/check"):
            return _envelope(_load_fixture("entitlement_required.json"))
        return httpx.Response(404, json={"ok": False, "error": {"code": "NOT_FOUND"}})

    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        shadow = client.shadow()
        cat = client.cloud_catalog(types="products,skills")
        ent = client.entitlement_check(content_type="skill", capability="skill:smoke")

    assert any(p.endswith("/device-api/shadow") for p in paths)
    assert any(p.endswith("/device-api/catalog") for p in paths)
    assert any(p.endswith("/device-api/entitlement/check") for p in paths)
    assert "shadow" in shadow
    assert "products" in cat
    assert ent["allowed"] is False
    assert ent["code"] == "ENTITLEMENT_REQUIRED"


def test_ack_config_updates_local_version(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/activate"):
            return _envelope(_load_fixture("activate_success.json"))
        if request.url.path.endswith("/config/ack"):
            body = json.loads(request.content)
            assert body["version"] == 7
            return _envelope({"acked": True})
        return httpx.Response(404, json={"ok": False, "error": {"code": "NOT_FOUND"}})

    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        assert client.config_version == 3 or client.config_version == 0
        client.ack_config(7)
        assert client.config_version == 7
        loaded = CredentialStore(cfg.credentials_path).load()
        assert loaded is not None
        assert loaded.config_version == 7


def test_run_cycle_acks_ota_and_config(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/activate"):
            return _envelope(_load_fixture("activate_success.json"))
        if request.url.path.endswith("/heartbeat"):
            return _envelope(_load_fixture("heartbeat_success.json"))
        if request.url.path.endswith("/shadow"):
            return _envelope({"shadow": {"desired": {}, "reported": {}}})
        if request.url.path.endswith("/ota/ack"):
            body = json.loads(request.content)
            assert body["jobId"] == "ota_job_fixture_1"
            assert body["status"] == "applied"
            return _envelope({"acked": True})
        if request.url.path.endswith("/config/ack"):
            body = json.loads(request.content)
            assert body["version"] == 7
            return _envelope({"acked": True})
        return httpx.Response(404, json={"ok": False, "error": {"code": "NOT_FOUND"}})

    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        out = run_cycle(client, auto_ack=True)
    assert out.errors == []
    assert out.ota_ack["acked"] is True
    assert out.config_ack["acked"] is True


def test_ack_ota_rejects_bad_status(tmp_path: Path):
    cfg = _cfg(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return _envelope(_load_fixture("activate_success.json"))

    with DeviceApiClient(cfg, transport=httpx.MockTransport(handler)) as client:
        client.activate()
        with pytest.raises(MoishomeApiError) as ei:
            client.ack_ota("job-1", "nope")
    assert ei.value.code == "VALIDATION_FAILED"


def test_activate_rejects_short_temp_key(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with DeviceApiClient(cfg, transport=httpx.MockTransport(lambda r: _envelope({}))) as client:
        with pytest.raises(MoishomeApiError) as ei:
            client.activate("SN-1", "short")
    assert ei.value.code == "VALIDATION_FAILED"


def test_client_does_not_register_local_catalog_route():
    """Cloud catalog must not be wired as a FastAPI /api/catalog proxy."""
    import inspect

    import app.moishome as moishome
    import app.moishome.client as client_mod
    from app.api import admin_api

    src = Path(client_mod.__file__).read_text(encoding="utf-8")
    assert "def cloud_catalog" in src
    assert '"/device-api/catalog"' in src
    # Package exposes no FastAPI router; local dfss catalog stays in admin_api.
    assert not hasattr(moishome, "router")
    assert not any(
        name == "admin_api" for name, _ in inspect.getmembers(moishome)
    )
    # Local route remains the board dfss catalogue (unchanged by this module).
    assert any(
        getattr(r, "path", None) == "/catalog" for r in admin_api.router.routes
    )
    # create_app still imports without moishome side effects.
    from app.main import create_app

    assert "moishome" not in create_app.__globals__


def test_cli_help():
    from app.moishome.cli import main

    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0


def test_platform_env_example_documents_gate():
    text = (FIXTURES / "platform.env.example").read_text(encoding="utf-8")
    assert "MOISHOME_BASE_URL" in text
    assert "CATALOG_ENTITLEMENT_GATE" in text
    assert "/api/v1" in text  # documents that client appends it
