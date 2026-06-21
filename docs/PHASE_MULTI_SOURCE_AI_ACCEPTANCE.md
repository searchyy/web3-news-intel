# 多源 AI 搜索阶段验收

本文档定义本期“多消息源 + 高级事件搜索 + DeepSeek AI 整理 + 飞书汇报 +
前端性能优化”的确定性验收和外部实时验收。

## 确定性验收

必须全部通过后才能报告 READY：

| 类别 | 命令或 Workflow | 说明 |
| --- | --- | --- |
| Python 质量 | `python -m ruff check .` | 代码风格和基础静态检查 |
| 类型检查 | `python -m mypy app scripts` | 后端与脚本类型检查 |
| 单元测试 | `python -m pytest tests/unit -q` | parser、搜索、AI、飞书、安全覆盖 |
| 集成测试 | `python -m pytest tests/integration -q` | PostgreSQL、Redis、Celery、搜索性能、mock E2E |
| Source 契约 | `python scripts/validate_sources.py sources.yaml --strict-contract --catalog-dir source_catalog` | 必要字段、安全默认值、状态枚举 |
| 安全验收 | `python scripts/security_acceptance.py` | workflow、metrics label、秘密扫描、安全文档 |
| 前端质量 | `npm run lint && npm run typecheck && npm run test` | React/Vite 测试 |
| 前端构建 | `npm run build` | 生产构建 |
| 前端性能 | `python scripts/frontend_performance_acceptance.py --dist-dir frontend/dist --assert-charts-isolated` | chunk/gzip 报告和图表库隔离 |
| Compose | `docker compose config --quiet && docker compose build && docker compose up -d` | 本地发布形态验收 |
| Mock DeepSeek | `ai-integration-mock` workflow | 不调用真实付费 API |
| Mock Feishu | compose mock E2E | 不向真实群发消息 |

## 外部实时来源验收

外部来源只通过 `live-source-canary` 定时或手动 workflow 执行，PR 不强制访问外网。
每源最多解析 10 条。结果上传为 JSON/Markdown artifact。

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

输出字段：

- `source_key`
- `adapter`
- `http_status`
- `content_type`
- `response_bytes`
- `body_sha256`
- `parsed_item_count`
- `newest_published_at`
- `sample_title`
- `original_url`
- `result`
- `error_reason`

状态定义：

- `PASS`：获取成功且解析出公开条目。
- `DEGRADED`：获取成功但解析数量为 0，或非致命结构变化。
- `ACCESS_DENIED`：401、403、robots 拒绝、登录墙或访问控制。
- `EMPTY`：公开响应为空。
- `PARSER_BROKEN`：字段缺失、JSON/HTML 结构变化导致 parser 失败。
- `NETWORK_FAILED`：DNS、超时、连接错误、响应过大。
- `DISABLED`：默认禁用或待审批来源，未发起抓取。

## 幂等验收

同一来源重复抓取两次必须满足：

- `duplicate events = 0`
- `duplicate event_sources = 0`
- `duplicate deliveries = 0`
- 同一来源重复抓取不增加 `confirmation_count`
- 正常轮询不重复处理历史数据

## CI Workflow

- `source-adapter-contracts.yml`：fixture、parser contract、source config validation。
- `ai-integration-mock.yml`：mock DeepSeek、预算、Celery、幂等、mock 飞书相关测试入口。
- `frontend-performance.yml`：前端 build、bundle size、chunk 检查、性能 artifact。
- `live-source-canary.yml`：手动/定时外部 canary，PR 不强制。
- `deepseek-test.yml`：仅 `workflow_dispatch`，使用受保护 GitHub Environment 和 secret，最多一次小型真实调用。

## 发布结论

只有以下条件全部满足才允许报告 `READY`：

- 现有功能没有回归。
- 新迁移在真实 PostgreSQL 上 upgrade/downgrade/upgrade 通过。
- Redis/Celery 通过。
- 搜索测试和 10,000 event 性能测试通过。
- DeepSeek mock 通过，真实测试未执行时明确标记 `NOT EXECUTED`。
- 飞书 mock 通过，真实飞书未执行时明确标记 `NOT EXECUTED`。
- 前端构建和性能门禁通过。
- Compose 通过。
- 没有提交秘密、数据库、dist、reports、node_modules 或缓存。
- 没有绕过目标站访问控制。
- 当前 feature HEAD 已推送。
