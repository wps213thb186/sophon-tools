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

from .client import DeviceApiClient
from .config import OpenPlatformConfig, default_credentials_path
from .credentials import CredentialStore
from .http import MoishomeApiError
from .identity import resolve_identity
from .runtime import HeartbeatLoop


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _state_dir() -> Path | None:
    raw = os.environ.get("MOISHOME_STATE_DIR") or os.environ.get("CV84X6_DATA_DIR")
    if not raw:
        return None
    p = Path(raw)
    # If CV84X6_DATA_DIR was given, from_env already nests state/; for MOISHOME_STATE_DIR
    # treat it as the state directory itself when the name says so.
    if os.environ.get("MOISHOME_STATE_DIR"):
        return p
    return None


def _data_dir() -> Path | None:
    raw = os.environ.get("CV84X6_DATA_DIR")
    return Path(raw) if raw and not os.environ.get("MOISHOME_STATE_DIR") else None


def _client() -> DeviceApiClient:
    return DeviceApiClient.from_env(state_dir=_state_dir(), data_dir=_data_dir())


def cmd_activate(_: argparse.Namespace) -> int:
    with _client() as client:
        creds = client.activate()
        print(_json(creds.public_dict()))
        if client.store is not None:
            print(f"persisted: {client.store.path}", file=sys.stderr)
    return 0


def cmd_heartbeat(_: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(_json(client.heartbeat()))
    return 0


def cmd_shadow(_: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(_json(client.shadow()))
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(
            _json(
                client.cloud_catalog(
                    model=args.model or "",
                    sku=args.sku or "",
                    isv=args.isv or "",
                    types=args.types or "",
                )
            )
        )
    return 0


def cmd_entitlement_check(args: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(
            _json(
                client.entitlement_check(
                    capability=args.capability or "",
                    content_id=args.content_id or "",
                    content_type=args.content_type or "",
                    product_id=args.product_id or "",
                )
            )
        )
    return 0


def cmd_ota_ack(args: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(_json(client.ack_ota(args.job_id, args.status, reason=args.reason or "")))
    return 0


def cmd_config_ack(args: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(_json(client.ack_config(args.version)))
    return 0


def cmd_compat(_: argparse.Namespace) -> int:
    with _client() as client:
        if client.credentials is None:
            client.activate()
        print(_json(client.compat()))
    return 0


def cmd_heartbeat_loop(args: argparse.Namespace) -> int:
    cfg = OpenPlatformConfig.from_env(state_dir=_state_dir(), data_dir=_data_dir())
    path = cfg.credentials_path or default_credentials_path(
        state_dir=_state_dir(), data_dir=_data_dir()
    )
    loop = HeartbeatLoop(cfg, store=CredentialStore(path))
    loop.run(once=args.once, max_ticks=args.max_ticks)
    return 0


def cmd_smoke(_: argparse.Namespace) -> int:
    steps: list[tuple[str, Any]] = []
    with _client() as client:
        try:
            if client.credentials is None or not client.credentials.credential:
                creds = client.activate()
            else:
                creds = client.credentials
            steps.append(("activate_or_load", creds.public_dict()))
            steps.append(("heartbeat", client.heartbeat()))
            steps.append(("shadow", client.shadow()))
            steps.append(("cloud_catalog", client.cloud_catalog(types="products")))
            steps.append(
                (
                    "entitlement_check",
                    client.entitlement_check(content_type="skill", capability="skill:smoke"),
                )
            )
        except MoishomeApiError as exc:
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
    cfg = OpenPlatformConfig.from_env(state_dir=_state_dir(), data_dir=_data_dir())
    identity = resolve_identity(cfg)
    path = cfg.credentials_path or default_credentials_path(
        state_dir=_state_dir(), data_dir=_data_dir()
    )
    store = CredentialStore(path)
    creds = store.load()
    print(
        _json(
            {
                "baseUrl": cfg.base_url or None,
                "apiRoot": f"{cfg.api_root()}" if cfg.base_url else None,
                "identity": {
                    "serial": identity.serial or None,
                    "sku": identity.sku or None,
                    "model": identity.model or None,
                },
                "credentialsPath": str(path),
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

    cat = sub.add_parser(
        "catalog",
        help="GET /device-api/catalog via cloud_catalog (NOT local /api/catalog)",
    )
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

    sub.add_parser(
        "smoke",
        help="activate/load → heartbeat → shadow → cloud_catalog → entitlement_check",
    ).set_defaults(func=cmd_smoke)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MoishomeApiError as exc:
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
