# 轮询与前端性能专项报告

日期：2026-06-22
分支：`fix/deepseek-config-polling-performance`

本报告只记录本轮稳定性修复的测量结果和代码级优化。真实 Redis/Celery、PostgreSQL 与 Compose 仍以 GitHub Actions 为准；本地未调用真实 DeepSeek，也未发送真实飞书消息。

## DeepSeek 配置

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 保存请求 URL | `PUT /api/admin/ai/providers/deepseek` | 不变 |
| Session Cookie | 已发送 | 已发送 |
| CSRF Header | 已发送 | 已发送 |
| 缺少 CSRF | 403 | 403 |
| 缺少 `FIELD_ENCRYPTION_KEY` | 英文/技术错误 | 中文可操作错误 |
| 空 Key 保存 | 有覆盖风险 | 保留旧 Key |
| 掩码 Key 保存 | 有覆盖风险 | 忽略掩码，保留旧 Key |
| 保存成功后 UI 刷新 | invalidate 后再 GET | 直接写入 Query 缓存 |
| 无 Key 获取模型 | 仍可能请求后端 | 前端拦截并提示先保存 Key |
| Key 存储位置 | 后端密文 | 后端密文 |
| localStorage/sessionStorage | 未发现 Key | 未发现 Key |

验证命令：

- `python -m pytest tests/unit/test_ai_deepseek_backend.py tests/integration/test_ai_admin_api.py -q`：通过
- `npm run test -- tests/ai-settings.test.tsx`：通过

## 消息源轮询

| 阶段 | 优化前 | 优化后 |
| --- | ---: | ---: |
| due-source 查询 p50 | 0.154 ms | 0.154 ms，查询逻辑保留 |
| due-source 查询 p95 | 0.229 ms | 0.229 ms，查询逻辑保留 |
| Scheduler enqueue | queued run 可能未 commit 即 enqueue | 先 commit，再 enqueue |
| 立即运行 | 直接 `fetch_source.delay(source.key)` | 先创建 `fetch_run`，再入队 |
| queue wait 可观测性 | 无 `queued_at`、`worker_started_at`、`task_id` | 新增三字段，可计算 queue wait |
| 同源 active fetch | 主要靠应用检查 | 应用检查 + 数据库部分唯一索引 |
| dev poll 本地请求 | 可能受代理环境影响 | `trust_env=False`，不走系统代理 |
| 304 处理 | adapters 不统一 | 通用 conditional fetch metadata |
| 连续失败 | 无统一 circuit breaker | 失败递增并设置 `circuit_open_until` |
| 403/401 | 有重复硬打风险 | 标记 access denied，默认禁用源 |

新增数据库字段：

- `fetch_runs.queued_at`
- `fetch_runs.worker_started_at`
- `fetch_runs.task_id`
- `fetch_runs.retry_after_until`

新增索引：

- `ix_fetch_runs_source_status_started`
- `ix_fetch_runs_task_id`
- `uq_fetch_runs_active_source`：`source_id` 上的部分唯一索引，仅覆盖 `queued`、`running`

本地测试结果：

- `tests/unit/test_scheduler_polling.py`：通过
- `tests/unit/test_dev_poll_sources.py`：通过
- `tests/unit/test_alembic_metadata_contract.py`：通过

本地限制：

- 当前本地 Redis/Celery 未启动，queue wait 和 worker start 的真实 p95 未执行。
- 本地 SQLite Alembic 文件烟测受残留锁文件影响，不能作为 PASS；迁移图为单 head，真实 PostgreSQL 迁移必须由 CI 验证。

## DB 到 UI 显示

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 事件页自动刷新 | 无周期刷新 | 页面可见时 10 秒刷新 |
| 页面隐藏 | 无专门控制 | 隐藏时停止事件列表刷新 |
| 搜索 debounce | 300 ms | 保留 |
| 相同参数重复请求 | 未发现业务重复 | 保持无重复 |
| facets 加载 | 进入事件页即请求 | 打开高级筛选抽屉时请求 |
| 保存 AI 配置后显示 | 依赖 refetch | 立即更新缓存 |

目标：

- `database commit -> /api/admin/events -> React Query 刷新 -> UI 显示`：页面可见时 10 秒内显示。
- 页面隐藏后非关键轮询为 0。

## 前端 API 请求

| 页面 | 优化前业务请求 | 优化后业务请求 |
| --- | ---: | ---: |
| Dashboard | 1 | 1 |
| Events 首屏 | 3 | 2 |
| Sources | 1 | 1 |
| AI Settings | 2 | 2 |

Events 首屏减少的请求是 `facets`，它现在延迟到高级筛选抽屉打开时加载。

React Query 策略集中在 `frontend/src/queryConfig.ts`：

- 401 不重试。
- 429 只有限重试。
- 页面可见性由 `usePageVisible()` 控制。
- 事件列表 refetch interval 为 10 秒，仅在页面可见时开启。
- AI 配置不自动轮询。

## Bundle 与加载

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 初始 JS/CSS gzip | 约 365.0 KB | 约 367.83 KB |
| 总 JS gzip | 约 734.2 KB | 约 739.83 KB |
| Login 是否包含 ECharts | 否 | 否 |
| Dashboard chart | 独立 charts chunk | `charts-CBJmW31n.js`，349.18 KB gzip，非初始资源 |
| AI 设置页进入 Login chunk | 否 | 否 |
| 飞书配置页进入 Events chunk | 否 | 否 |

当前最大业务相关 chunk：

- `AiSettingsPage-BjHWxoEa.js`：4.40 KB gzip
- `EventsPage-DkGoCpDN.js`：6.44 KB gzip
- `FeishuGroupsPage-CXBlMsGS.js`：5.45 KB gzip

本轮没有引入新业务页面，也没有移除现有懒加载；主要优化集中在请求策略和延迟 facets。`antd` vendor 仍是初始资源主因，当前 gzip 为 299.78 KB。

## 开发验收速度

新增 `scripts/dev_acceptance.ps1`：

- `-Quick`：检查 changed Python、相关 mypy、相关 pytest、前端 typecheck 和相关 Vitest。
- `-Backend`：运行后端 lint、mypy、unit、integration。
- `-Frontend`：运行前端 lint、typecheck、test、build。
- `-Full`：运行完整本地门禁。

已验证：

- PowerShell 语法解析通过。
- 相关 pytest、Vitest、typecheck 通过。

## 待 CI 验证

以下项目本地不能报告 PASS，必须由 GitHub Actions 或可用 Docker 环境确认：

- 真实 PostgreSQL `alembic upgrade head / downgrade -1 / upgrade head`
- PostgreSQL 部分唯一索引 `uq_fetch_runs_active_source`
- 真实 Redis/Celery queue wait p95
- worker restart 后幂等
- Compose Mock DeepSeek + Mock 飞书 E2E
- 真实前端生产 build gzip 后对比
