# 多消息源、DeepSeek AI、事件搜索、飞书汇报与前端性能优化

本 PR 是堆叠 PR，基础分支应选择 `feat/feishu-admin-dashboard`，不要直接对 `main` 创建重复大 PR，除非父分支已经合并进 `main`。

## 变更摘要

- 新增统一 source catalog，并接入交易所官方公告源、中文媒体源和英文媒体源。
- 新增高级事件搜索、facets、保存筛选和 PostgreSQL 搜索索引。
- 新增 DeepSeek AI Provider、密钥加密保存、模型列表、连接测试、AI insight 和 mock 集成测试。
- 新增飞书汇报 schedule、预览、测试发送、幂等 delivery 和 Mock 飞书 Compose E2E。
- 补齐飞书群组页的汇报规则管理 UI。
- 优化前端路由懒加载、ECharts 隔离、Vite chunk 分割和性能门禁。

## 安全约束

- 不提交 API Key、飞书密钥、Webhook、`.env`、数据库、`dist`、`node_modules`、`reports` 或缓存。
- CI 不调用真实 DeepSeek，不发送真实飞书消息。
- 不绕过 Cloudflare、验证码、403、429、登录墙、付费墙或任何访问控制。
- 不可用来源保持 disabled，并在 catalog/canary 中记录真实状态。

## 验收重点

- `quality`
- `frontend-quality`
- `postgres-integration`
- `redis-celery-integration`
- `compose-acceptance`
- `source-adapter-contracts`
- `ai-integration-mock`
- `frontend-performance`

只有上述确定性 CI 对当前 HEAD 全部成功，并且真实 PostgreSQL 迁移、10,000 event 搜索性能、真实 Redis/Celery、完整 Compose Mock E2E 均通过后，才可报告 READY。
