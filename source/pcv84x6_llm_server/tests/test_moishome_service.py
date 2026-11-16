"""Service enablement + /moishome management routes (mock transport)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.moishome.service import MoishomeBridge

FIXTURES = Path(__file__).parent / "fixtures" / "moishome"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _envelope(data):
    return httpx.Response(200, json={"ok": True, "data": data, "requestId": "t"})


@pytest.fixture()
def mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/activate"):
            return _envelope(_load("activate_success.json"))
        if path.endswith("/heartbeat"):
            return _envelope(_load("heartbeat_success.json"))
        if path.endswith("/shadow"):
            return _envelope({"shadow": {"desired": {}, "reported": {}}})
        if path.endswith("/catalog"):
            return _envelope(_load("catalog_success.json"))
        if path.endswith("/entitlement/check"):
            return _envelope(_load("entitlement_required.json"))
        if path.endswith("/ota/ack") or path.endswith("/config/ack"):
            return _envelope({"acked": True})
        return httpx.Response(404, json={"ok": False, "error": {"code": "NOT_FOUND"}})

    return httpx.MockTransport(handler)


def test_bridge_disabled_without_base_url(tmp_path, monkeypatch):
    monkeypatch.delenv("MOISHOME_BASE_URL", raising=False)
    bridge = MoishomeBridge.from_state_dir(tmp_path)
    assert bridge.enabled is False
    st = bridge.status()
    assert st["enabled"] is False
    assert st["baseUrl"] is None


def test_create_app_offline_has_moishome_status(tmp_path, monkeypatch):
    monkeypatch.delenv("MOISHOME_BASE_URL", raising=False)
    monkeypatch.delenv("MOISHOME_HEARTBEAT", raising=False)
    models = tmp_path / "models"
    models.mkdir()
    app = create_app(
        data_dir=tmp_path / "data",
        models_dir=models,
        backend_kind="mock",
        web_dir=tmp_path / "web",
    )
    assert app.state.moishome.enabled is False
    with TestClient(app) as client:
        assert client.post("/api/login", json={"password": "admin"}).status_code == 200
        r = client.get("/moishome/status")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        # Local dfss catalog still present and separate.
        cat = client.get("/api/catalog")
        assert cat.status_code == 200
    app.state.audit.close()
    app.state.inference_log.close()


def test_moishome_routes_with_mock_transport(tmp_path, monkeypatch, mock_transport):
    monkeypatch.setenv("MOISHOME_BASE_URL", "https://platform.test")
    monkeypatch.setenv("MOISHOME_SERIAL", "SN-TEST-001")
    monkeypatch.setenv("MOISHOME_TEMP_KEY", "temp-key-16chars!")
    monkeypatch.setenv("MOISHOME_SKU", "64G")
    monkeypatch.setenv("MOISHOME_HEARTBEAT", "0")

    models = tmp_path / "models"
    models.mkdir()
    app = create_app(
        data_dir=tmp_path / "data",
        models_dir=models,
        backend_kind="mock",
        web_dir=tmp_path / "web",
    )
    # Swap in mock transport after create_app built the bridge from env.
    app.state.moishome.transport = mock_transport
    assert app.state.moishome.enabled is True

    with TestClient(app) as client:
        assert client.post("/api/login", json={"password": "admin"}).status_code == 200
        st = client.get("/moishome/status").json()
        assert st["enabled"] is True
        act = client.post("/moishome/activate")
        assert act.status_code == 200, act.text
        assert act.json()["data"]["credentialPrefix"]
        hb = client.post("/moishome/heartbeat")
        assert hb.status_code == 200, hb.text
        assert hb.json()["data"]["online"] is True
        sh = client.get("/moishome/shadow")
        assert sh.status_code == 200
        cat = client.get("/moishome/cloud-catalog")
        assert cat.status_code == 200
        assert "products" in cat.json()["data"]
        ent = client.post(
            "/moishome/entitlement-check",
            json={"contentType": "skill", "capability": "skill:smoke"},
        )
        assert ent.status_code == 200
        assert ent.json()["data"]["allowed"] is False
        # Prove local catalog path is unchanged / still distinct.
        local = client.get("/api/catalog")
        assert local.status_code == 200
        assert local.json() != cat.json()["data"]
        cycle = client.post("/moishome/cycle")
        assert cycle.status_code == 200
        assert cycle.json()["ok"] is True

    cred_path = tmp_path / "data" / "moishome_device.json"
    assert cred_path.exists()
    app.state.audit.close()
    app.state.inference_log.close()


def test_smoke_mock_script_runs():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "moishome_smoke_mock.py"
    # Execute as __main__ would; assert exit code via run_path return isn't used —
    # call the module's main after loading by path.
    spec = importlib.util.spec_from_file_location("moishome_smoke_mock", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0
