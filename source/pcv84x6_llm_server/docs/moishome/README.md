# MoisHome 开放平台对接层

独立可选库 `app/moishome`：配置、凭证落盘（`state/moishome_device.json`）、信封 HTTP、`DeviceApiClient`、CLI。  
**默认不改** `create_app`；未设 `MOISHOME_BASE_URL` 时服务仍完全离线。

云端目录方法名：`cloud_catalog`（`GET /api/v1/device-api/catalog`）。  
**不要**与本地管理面 `GET /api/catalog`（dfss）混淆。

契约：开放平台仓 `docs/zetao-device-api-contract.md`。  
设计/冒烟：Project Context 中 `zetao-device-api-integration-design.md` / `zetao-device-api-smoke-checklist.md`。

## 环境变量

| 变量 | 说明 |
|---|---|
| `MOISHOME_BASE_URL` | 平台根 URL，**不含** `/api/v1` |
| `MOISHOME_SERIAL` / `MOISHOME_TEMP_KEY` | SN 与激活临时 K（≥16） |
| `MOISHOME_SKU` / `MOISHOME_FIRMWARE` / `MOISHOME_CONFIG_VERSION` | 心跳属性 |
| `MOISHOME_HTTP_TIMEOUT_S` | 默认 15 |

平台侧联调目录闸：`CATALOG_ENTITLEMENT_GATE=0`（设备客户端不读此变量）。

## 用法

```bash
export MOISHOME_BASE_URL=https://<host>
export MOISHOME_SERIAL=SN-…
export MOISHOME_TEMP_KEY='…'
# 可选：export MOISHOME_STATE_DIR=/tmp/moishome-state

cd source/pcv84x6_llm_server
PYTHONPATH=. python3 - <<'PY'
from pathlib import Path
from app.moishome import DeviceApiClient
with DeviceApiClient.from_env(state_dir=Path("/tmp/moishome-state")) as c:
    c.activate()
    print(c.heartbeat())
    print(c.shadow())
    print(c.cloud_catalog(types="products"))
    print(c.entitlement_check(content_type="skill"))
PY

# 或 CLI
python -m app.moishome.cli smoke
```

Fixtures：`tests/fixtures/moishome/`。  
单测：`pytest -q tests/test_moishome_device_api.py`
