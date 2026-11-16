# MoisHome 开放平台对接层

独立模块 `app/moishome`：配置、凭证落盘、device-api HTTP client、CLI。  
**不**接入推理核心；本地 `GET /api/catalog`（dfss）与云端 `GET /device-api/catalog` 勿混淆。

契约：开放平台仓 `docs/zetao-device-api-contract.md`（前缀 `/api/v1`）。

## 快速用法

```bash
export MOISHOME_BASE_URL=https://<host>/api/v1
export MOISHOME_SERIAL=SN-…
export MOISHOME_TEMP_KEY='…'          # ≥16；activate 后不再需要
export MOISHOME_CREDENTIALS_PATH=/tmp/moishome-credentials.json

cd source/pcv84x6_llm_server
python -m app.moishome.cli status
python -m app.moishome.cli activate
python -m app.moishome.cli heartbeat
python -m app.moishome.cli catalog
python -m app.moishome.cli entitlement-check --content-type skill --capability skill:foo
python -m app.moishome.cli smoke
```

单元测试：`pytest tests/test_moishome_device_api.py -q`
