"""Minimal runnable MoisHome device-api CLI.

Usage (from ``source/pcv84x6_llm_server``)::

    python -m app.moishome.cli activate
    python -m app.moishome.cli heartbeat
    python -m app.moishome.cli smoke
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .client import DeviceApiClient, DeviceApiError
from .config import OpenPlatformConfig
from .credentials import CredentialStore, DeviceCredentials
from .identity import require_serial, resolve_identity
from .runtime import HeartbeatLoop, activate_and_persist, load_or_activate, run_cycle


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _cfg() -> OpenPlatformConfig:
    data_dir = os.environ.get("CV84X6_DATA_DIR")
    return OpenPlatformConfig.from_env(Path(data_dir) if data_dir else None)


def _store(cfg: OpenPlatformConfig) -> CredentialStore:
    path = cfg.credentials_path
    if path is None:
        path = Path(os.environ.get("TMPDIR") or "/tmp") / "moishome-credentials.json"
    return CredentialStore(path)


def _client_with_creds(cfg: OpenPlatformConfig) -> tuple[DeviceApiClient, DeviceCredentials, CredentialStore]:
    identity = resolve_identity(cfg)
    require_serial(identity)
    store = _store(cfg)
    client = DeviceApiClient(cfg, serial=identity.serial)
    creds = load_or_activate(cfg, client, store, identity)
    return client, creds, store


def cmd_activate(_: argparse.Namespace) -> int:
    cfg = _cfg()
    identity = resolve_identity(cfg)
    serial = require_serial(identity)
    if not cfg.temp_key:
        print("MOISHOME_TEMP_KEY required", file=sys.stderr)
        return 2
    store = _store(cfg)
    with DeviceApiClient(cfg, serial=serial) as client:
        creds = activate_and_persist(client, store, serial=serial, temp_key=cfg.temp_key)
    print(_json(creds.public_dict()))
    print(f"persisted: {store.path}", file=sys.stderr)
    return 0


def cmd_heartbeat(_: argparse.Namespace) -> int:
    cfg = _cfg()
    identity = resolve_identity(cfg)
    client, _, _ = _client_with_creds(cfg)
    try:
        out = run_cycle(client, cfg, identity, pull_shadow=False, auto_ack=False)
        if out.errors:
            print("\n".join(out.errors), file=sys.stderr)
            return 1
        print(_json(out.heartbeat))
        return 0
    finally:
        client.close()


def cmd_shadow(_: argparse.Namespace) -> int:
    cfg = _cfg()
    client, _, _ = _client_with_creds(cfg)
    try:
        print(_json(client.get_shadow()))
        return 0
    finally:
        client.close()


def cmd_catalog(args: argparse.Namespace) -> int:
    cfg = _cfg()
    identity = resolve_identity(cfg)
    client, _, _ = _client_with_creds(cfg)
    try:
        data = client.get_catalog(
            model=args.model or identity.model,
            sku=args.sku or identity.sku,
            isv=args.isv or "",
            types=args.types or "",
        )
        print(_json(data))
        return 0
    finally:
        client.close()


def cmd_entitlement_check(args: argparse.Namespace) -> int:
    cfg = _cfg()
    client, _, _ = _client_with_creds(cfg)
    try:
        data = client.check_entitlement(
            capability=args.capability or "",
            content_id=args.content_id or "",
            content_type=args.content_type or "",
            product_id=args.product_id or "",
        )
        print(_json(data))
        return 0
    finally:
        client.close()


def cmd_ota_ack(args: argparse.Namespace) -> int:
    cfg = _cfg()
    client, _, _ = _client_with_creds(cfg)
    try:
        print(_json(client.ack_ota(args.job_id, args.status, reason=args.reason or "")))
        return 0
    finally:
        client.close()


def cmd_config_ack(args: argparse.Namespace) -> int:
    cfg = _cfg()
    client, _, _ = _client_with_creds(cfg)
    try:
        print(_json(client.ack_config(args.version)))
        return 0
    finally:
        client.close()


def cmd_compat(_: argparse.Namespace) -> int:
    cfg = _cfg()
    client, _, _ = _client_with_creds(cfg)
    try:
        print(_json(client.get_compat()))
        return 0
    finally:
        client.close()


def cmd_heartbeat_loop(args: argparse.Namespace) -> int:
    cfg = _cfg()
    loop = HeartbeatLoop(cfg, store=_store(cfg))
    loop.run(once=args.once, max_ticks=args.max_ticks)
    return 0


def cmd_smoke(_: argparse.Namespace) -> int:
    """Ordered smoke against a live platform (requires env fixtures)."""
    cfg = _cfg()
    identity = resolve_identity(cfg)
    require_serial(identity)
    store = _store(cfg)
    steps: list[tuple[str, Any]] = []

    with DeviceApiClient(cfg, serial=identity.serial) as client:
        try:
            creds = load_or_activate(cfg, client, store, identity)
            steps.append(("activate_or_load", creds.public_dict()))
            hb = client.heartbeat(
                firmware=cfg.firmware,
                config_version=cfg.config_version,
                attributes={"sku": identity.sku, "model": identity.model}
                if (identity.sku or identity.model)
                else None,
            )
            steps.append(("heartbeat", hb))
            steps.append(("shadow", client.get_shadow()))
            steps.append(
                (
                    "catalog",
                    client.get_catalog(model=identity.model, sku=identity.sku),
                )
            )
            steps.append(
                (
                    "entitlement_check",
                    client.check_entitlement(
                        content_type="skill",
                        capability="skill:smoke",
                    ),
                )
            )
        except DeviceApiError as exc:
            print(
                _json(
                    {
                        "ok": False,
                        "failed": steps[-1][0] if steps else "start",
                        "code": exc.code,
                        "message": str(exc),
                        "status": exc.status,
                        "requestId": exc.request_id,
                        "completed": [{"step": n, "ok": True} for n, _ in steps],
                    }
                ),
                file=sys.stderr,
            )
            return 1

    print(_json({"ok": True, "steps": [{"name": n, "data": d} for n, d in steps]}))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    cfg = _cfg()
    store = _store(cfg)
    identity = resolve_identity(cfg)
    creds = store.load()
    print(
        _json(
            {
                "baseUrl": cfg.base_url or None,
                "identity": {
                    "serial": identity.serial or None,
                    "sku": identity.sku or None,
                    "model": identity.model,
                },
                "credentialsPath": str(store.path),
                "activated": bool(creds and creds.credential),
                "credentials": creds.public_dict() if creds and creds.credential else None,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.moishome.cli", description="MoisHome device-api client")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show resolved identity / credential summary").set_defaults(
        func=cmd_status
    )
    sub.add_parser("activate", help="POST /device-api/activate and persist credential").set_defaults(
        func=cmd_activate
    )
    sub.add_parser("heartbeat", help="POST /device-api/heartbeat").set_defaults(func=cmd_heartbeat)
    sub.add_parser("shadow", help="GET /device-api/shadow").set_defaults(func=cmd_shadow)
    sub.add_parser("compat", help="GET /device-api/compat").set_defaults(func=cmd_compat)

    cat = sub.add_parser("catalog", help="GET /device-api/catalog (cloud; not local /api/catalog)")
    cat.add_argument("--model", default="")
    cat.add_argument("--sku", default="")
    cat.add_argument("--isv", default="")
    cat.add_argument("--types", default="")
    cat.set_defaults(func=cmd_catalog)

    ent = sub.add_parser("entitlement-check", help="POST /device-api/entitlement/check")
    ent.add_argument("--capability", default="")
    ent.add_argument("--content-id", default="")
    ent.add_argument("--content-type", default="")
    ent.add_argument("--product-id", default="")
    ent.set_defaults(func=cmd_entitlement_check)

    ota = sub.add_parser("ota-ack", help="POST /device-api/ota/ack")
    ota.add_argument("--job-id", required=True)
    ota.add_argument("--status", required=True, choices=("downloading", "applied", "failed"))
    ota.add_argument("--reason", default="")
    ota.set_defaults(func=cmd_ota_ack)

    cack = sub.add_parser("config-ack", help="POST /device-api/config/ack")
    cack.add_argument("--version", type=int, required=True)
    cack.set_defaults(func=cmd_config_ack)

    loop = sub.add_parser("heartbeat-loop", help="periodic heartbeat (+ shadow/ACK)")
    loop.add_argument("--once", action="store_true")
    loop.add_argument("--max-ticks", type=int, default=None)
    loop.set_defaults(func=cmd_heartbeat_loop)

    sub.add_parser("smoke", help="activate/load → heartbeat → shadow → catalog → entitlement").set_defaults(
        func=cmd_smoke
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DeviceApiError as exc:
        print(
            _json(
                {
                    "ok": False,
                    "code": exc.code,
                    "message": str(exc),
                    "status": exc.status,
                    "requestId": exc.request_id,
                }
            ),
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
