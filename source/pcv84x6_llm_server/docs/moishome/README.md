# MoisHome 开放平台对接层

独立可选库 + 服务挂载：`app/moishome`（配置 / 凭证 / HTTP / client）与管理面 **`/moishome/*`**。  
**默认离线**：未设 `MOISHOME_BASE_URL` 时不访问云端，不挡 `create_app`。  
**不侵入**推理热路径；**不改**本地 `GET /api/catalog`（dfss）。

云端目录：`DeviceApiClient.cloud_catalog` / `GET /moishome/cloud-catalog`  
→ 平台 `GET /api/v1/device-api/catalog`。

契约：开放平台仓 `docs/zetao-device-api-contract.md`。  
设计/冒烟：Project `zetao-device-api-integration-design.md` / `zetao-device-api-smoke-checklist.md`。

## 启用条件

| 变量 | 作用 |
|---|---|
| `MOISHOME_BASE_URL` | **启用**对接（平台根 URL，**不含** `/api/v1`） |
| `MOISHOME_SERIAL` | 设备 SN |
| `MOISHOME_TEMP_KEY` | 仅激活用（≥16）；成功后靠 `state/moishome_device.json` |
| `MOISHOME_SKU` / `MOISHOME_FIRMWARE` / `MOISHOME_CONFIG_VERSION` | 心跳属性 |
| `MOISHOME_HTTP_TIMEOUT_S` | 默认 15 |
| `MOISHOME_HEARTBEAT` | `1` 时在 lifespan 起周期心跳；默认关 |
| `MOISHOME_HEARTBEAT_INTERVAL_S` | 默认 60 |

凭证路径：`<state_dir>/moishome_device.json`（随数据目录）。  
平台侧目录闸（设备不读）：`CATALOG_ENTITLEMENT_GATE=0`。

示例 env：`tests/fixtures/moishome/platform.env.example`（占位，勿填真密钥入库）。

## 服务内调用（管理鉴权）

配置齐全并启动服务后（admin cookie 或 admin key）：

```bash
curl -s -b cookie.txt http://127.0.0.1:8000/moishome/status
curl -s -b cookie.txt -X POST http://127.0.0.1:8000/moishome/activate
curl -s -b cookie.txt -X POST http://127.0.0.1:8000/moishome/heartbeat
curl -s -b cookie.txt http://127.0.0.1:8000/moishome/shadow
curl -s -b cookie.txt 'http://127.0.0.1:8000/moishome/cloud-catalog?types=products'
curl -s -b cookie.txt -X POST http://127.0.0.1:8000/moishome/entitlement-check \
  -H 'Content-Type: application/json' \
  -d '{"contentType":"skill","capability":"skill:smoke"}'
```

## CLI / 库

```bash
cd source/pcv84x6_llm_server
PYTHONPATH=. python3 -m app.moishome.cli status
PYTHONPATH=. python3 -m app.moishome.cli smoke
```

## 无真 SN / 平台时的可跑证明（mock）

```bash
cd source/pcv84x6_llm_server
PYTHONPATH=. python3 scripts/moishome_smoke_mock.py
pytest -q tests/test_moishome_device_api.py tests/test_moishome_service.py
```

`moishome_smoke_mock.py` 只用 `tests/fixtures/moishome/*.json` + `httpx.MockTransport`，**不**证明真网可达。

### 真机替换点

1. 复制 `tests/fixtures/moishome/platform.env.example` → 私有路径  
2. 填入真实 `MOISHOME_BASE_URL` / `MOISHOME_SERIAL` / `MOISHOME_TEMP_KEY`  
3. `set -a && source /path/to/moishome.env && set +a`  
4. `python -m app.moishome.cli smoke` 或启服务后打 `/moishome/*`  
5. 若目录被授权闸挡住：平台设 `CATALOG_ENTITLEMENT_GATE=0`（联调临时）
