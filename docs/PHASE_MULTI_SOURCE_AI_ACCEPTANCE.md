# 多源 AI 搜索阶段验收

本文档定义“多消息源 + 高级事件搜索 + DeepSeek AI 整理 + 飞书汇报 + 前端性能优化”的确定性验收和外部实时验收。

## 确定性验收

只有全部通过后才能报告 READY。

| 类别 | 命令或 Workflow | 当前本机结果 |
| --- | --- | --- |
| Python 质量 | `python -m ruff check .` | PASS |
| 类型检查 | `python -m mypy app scripts` | PASS |
| 单元测试 | `python -m pytest tests/unit -q` | PASS |
| fixture 集成测试 | `python -m pytest tests/integration -q -m "not postgres and not redis and not celery and not compose and not live"` | PASS |
| Source 契约 | `python scripts/validate_sources.py sources.yaml --strict-contract --catalog-dir source_catalog` | PASS，legacy source_group 有 warning |
| 安全验收 | `python scripts/security_acceptance.py` | PASS，部分低基数指标为待业务接线 warning |
| 前端质量 | `npm run lint && npm run typecheck && npm run test` | PASS |
| 前端构建 | `npm run build` | PASS |
| 前端性能 | `python scripts/frontend_performance_acceptance.py --dist-dir frontend/dist --assert-charts-isolated` | PASS |
| Compose | `docker compose config --quiet && docker compose build && docker compose up -d` | NOT EXECUTED，本机无 docker 命令 |
| Mock DeepSeek | `tests/unit/test_ai_deepseek_backend.py`、`tests/integration/test_ai_admin_api.py`、`ai-integration-mock` workflow | 本机 mock 测试 PASS；workflow 未在 GitHub 执行 |
| Mock Feishu | 飞书汇报单元和 fixture 集成测试 | PASS；完整 compose mock E2E 未执行 |

## 外部实时来源验收

外部来源只通过 `live-source-canary` 手动或定时 workflow 执行，PR 不强制访问外网。每源最多解析 10 条，结果上传为 JSON/Markdown artifact。

目标交易所：

- Coinbase
- Binance
- Kraken
- Bitget
- OKX
- Bybit
- Bitstamp
- Gate
- MEXC
- HashKey

目标媒体：

- BlockBeats
- Foresight
- PANews
- Odaily
- ChainCatcher
- TechFlow
- CoinDesk
- Decrypt

本机已执行两次受控 canary，覆盖 `sources.yaml` 和 `source_catalog/`。外部网络存在波动，结果摘要：

- 稳定 PASS：SEC、CFTC、Ethereum Blog、BlockBeats、Decrypt、Cointelegraph。
- 曾经 PASS 但第二次网络失败：legacy Binance、legacy OKX。
- DEGRADED：DefiLlama Hacks、catalog OKX。
- NETWORK_FAILED：CoinDesk、catalog Binance。
- DISABLED：未通过稳定公开入口确认的交易所和媒体候选源。

未使用 fixture 伪装 live success，未绕过访问控制。

## 幂等验收

目标：

- `duplicate events = 0`
- `duplicate event_sources = 0`
- `duplicate deliveries = 0`
- 同一来源重复抓取不增加 `confirmation_count`
- 正常轮询不重复处理历史数据

本机未完成两次真实抓取和完整 compose mock E2E，因此该项不能计为 READY。

## CI Workflow

- `source-adapter-contracts.yml`：fixture、parser contract、source config validation。
- `ai-integration-mock.yml`：mock DeepSeek、预算、Celery、幂等和 mock 飞书相关测试入口。
- `frontend-performance.yml`：前端 build、bundle size、chunk 检查、性能 artifact。
- `live-source-canary.yml`：手动/定时外部 canary，PR 不强制。
- `deepseek-test.yml`：仅 `workflow_dispatch`，使用受保护 GitHub Environment 和 secret，最多一次小型真实调用。

## 发布结论规则

只有以下条件全部满足才允许报告 `READY`：

- 现有功能没有回归。
- 新迁移在真实 PostgreSQL 中 upgrade/downgrade/upgrade 通过。
- Redis/Celery 通过。
- 搜索测试和 10,000 event 性能测试通过。
- DeepSeek mock 通过，真实测试未执行时明确标记 `NOT EXECUTED`。
- 飞书 mock 通过，真实飞书未执行时明确标记 `NOT EXECUTED`。
- 前端构建和性能门禁通过。
- Compose 通过。
- 没有提交秘密、数据库、dist、reports、node_modules 或缓存。
- 没有绕过目标站访问控制。
- 当前 feature HEAD 已推送。

当前本机结论：`BLOCKED`，阻塞项是 Docker/Compose、真实 PostgreSQL 迁移与 10,000 event 性能、Redis/Celery、完整 mock E2E 和 GitHub CI 尚未执行。
