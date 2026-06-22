# DeepSeek、轮询与前端性能修复基线

生成时间：2026-06-22
修复分支：`fix/deepseek-config-polling-performance`
基础分支：`feat/multi-source-ai-search-performance`，多来源 AI 功能尚未合并到 `main`。

本文记录第一波只读 Agent 的复现证据。后续修复必须以这些实测事实为准，不得只按猜测改代码。

## DeepSeek 配置链路

- 保存按钮请求 URL：`/api/admin/ai/providers/deepseek`
- 本地实际访问：`http://127.0.0.1:5174/api/admin/ai/providers/deepseek`
- 代理后端路由：`PUT /api/admin/ai/providers/deepseek`
- 请求方法：`PUT`
- 请求体字段：`provider`、`enabled`、`auto_process_enabled`、`api_base`、`model`、`max_tokens`、`timeout_seconds`、`max_concurrency`、`daily_token_budget`、`daily_request_budget`、`auto_minimum_severity`、`thinking_enabled`、`api_key`
- Session Cookie：已发送，前端 `api()` 使用 `credentials: "include"`
- CSRF Header：已发送，字段为 `X-CSRF-Token`
- 无 CSRF 复现：`403 {"detail":"CSRF 校验失败"}`
- 有 CSRF 且提交测试 key 复现：`400 {"detail":"ai_configuration_error: FIELD_ENCRYPTION_KEY is required for AI secrets"}`

真实根因：

当前本地 DeepSeek Key 保存失败是因为后端缺少 `FIELD_ENCRYPTION_KEY`。请求已经通过 Session 和 CSRF 校验，并进入 `AIService.update_provider_config()`；失败发生在 `_field_encryptor()` 创建加密器时，事务提交前中断，因此 Key 没有落库。

本地运行库状态：

- DB：`C:/Users/search/.codex/memories/web3_news_site_run.sqlite3`
- `FIELD_ENCRYPTION_KEY`：未配置
- `ai_provider_configs.deepseek`：存在 1 行
- `api_key_ciphertext`：空
- `api_key_fingerprint`：空
- `last_error_sanitized`：`ai_configuration_error: DeepSeek API Key is not configured`

其他结论：

- 环境变量不会覆盖已存在的数据库配置；只在 provider 行不存在时作为初始化默认值。
- 项目没有从 `DEEPSEEK_API_KEY` 环境变量读取真实 Key 的链路，Key 只能由管理后台写入密文。
- 模型接口在无 Key 时返回配置错误，未调用真实 DeepSeek。
- 前端没有把 Key 写入 `localStorage` 或 `sessionStorage`。
- 保存、删除、测试连接后的 React Query 缓存需要精确更新或失效，不能全局刷新。

修复契约：

- `GET /api/admin/ai/providers/deepseek` 永远不返回 Key 原文，只返回 `api_key_configured`、`api_key_masked`、`api_key_fingerprint` 等脱敏字段。
- `PUT /api/admin/ai/providers/deepseek` 未传 Key、传空字符串或传掩码值时保留旧 Key。
- 传入新 Key 时必须使用 `FIELD_ENCRYPTION_KEY` 加密保存；缺少或无效时返回明确中文错误，禁止明文降级。
- `DELETE /api/admin/ai/providers/deepseek/key` 是唯一删除 Key 的方式。
- `GET /api/admin/ai/providers/deepseek/models` 必须使用数据库中已保存并解密的 Key。
- `POST /api/admin/ai/providers/deepseek/test` 不得自动开启 AI，不得自动修改预算。
- CI 和本地验收只使用 Mock DeepSeek，不调用真实付费 API。

## 消息源轮询链路

本地只读复现时没有真实 Redis/Celery：

- `REDIS_URL` 指向 `127.0.0.1:1`
- 6379 无监听
- Celery/Redis 链路：`NOT EXECUTED`

当前本地实际运行的是开发链路：

`scripts/dev_poll_sources.py -> POST /dev/run-source/{source} -> scripts/dev_api.py -> _fetch_source()`

轮询基线：

- sources：32
- enabled sources：13
- fetch_runs：30
- success fetch_runs：26
- failed fetch_runs：4
- raw_documents：30
- events：732
- event_sources：732
- Celery beat 计划：每 60 秒调用 `poll_sources`
- dev poll 间隔：300 秒
- 最近三源间隔：303-305 秒
- due-source 查询：13 个 enabled candidates，当前 10 due
- due-source 查询 p50：0.154 ms
- due-source 查询 p95：0.229 ms
- Celery queue wait：`NOT EXECUTED`

已识别问题：

- 本地曾出现重复 `dev_poll_sources.py` 进程，导致重复抓取风险。
- `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 会让本机 `127.0.0.1:59134` 请求走代理并返回 502，需要开发轮询明确 `trust_env=False`。
- Celery path 中 queued run 必须先 commit 再 enqueue，避免 worker 先于事务可见性启动。
- `_fetch_source(source_key)` 需要 per-source active lock，避免多个 scheduler 或手动运行并发抓同一 source。
- adapters 需要通用 ETag/Last-Modified 支持，304 不应重新 parse 或创建 raw document/event。
- 失败源需要 `consecutive_failures` 和 `circuit_open_until`，403/401 应标记 access denied，429 必须尊重 `Retry-After`。

轮询修复契约：

- “立即运行”API p95 目标小于 500 ms，只负责快速入队。
- worker 可用时任务开始 p95 目标小于 5 秒。
- 同一 source 同一时间只允许一个 active fetch。
- queued run 必须先 commit，再 enqueue。
- 需要记录或可计算：scheduler tick、due query、enqueue latency、queue wait、worker start、fetch duration、parse duration、database commit duration。
- 不得因追求速度绕过 403、429、Cloudflare、验证码或访问控制。

## 前端请求与性能基线

页面 API 数量：

| 页面 | 已登录 SPA 进入 | 重复业务请求 | 直达/刷新进入 | 1 分钟内同参数重复 |
| --- | ---: | ---: | ---: | ---: |
| Dashboard | 1：`/api/admin/dashboard/summary` | 0 | 3：2 次 `/auth/me` + summary | 业务 0；`/auth/me` 多 1 次 |
| Events | 3：events、facets、saved-searches | 0 | 5：2 次 `/auth/me` + 3 个业务 API | 业务 0；`/auth/me` 多 1 次 |
| Sources | 1：`/api/admin/sources` | 0 | 3：2 次 `/auth/me` + sources | 业务 0；`/auth/me` 多 1 次 |
| AI Settings | 2：DeepSeek config、AI runs | 0 | 4：2 次 `/auth/me` + 2 个业务 API | 业务 0；`/auth/me` 多 1 次 |

`/auth/me` 双请求主要来自 Vite dev + `React.StrictMode`，生产构建通常不复现。

React Query 基线：

- 全局 `staleTime=30000`
- 全局 `refetchOnWindowFocus=false`
- 全局 `retry=1`
- 无全局 `refetchInterval`
- Dashboard summary：`staleTime=30s`
- Events list：`staleTime=15s`
- Events facets：`staleTime=300s`，`retry=false`
- Saved searches：`staleTime=60s`，`retry=false`
- AI config/runs：`staleTime=30s`，`retry=false`
- AI models：手动 refetch，`enabled=false`，`staleTime=600s`

已识别问题：

- 代码默认 dev proxy 目标是 `http://127.0.0.1:8000`，本地当前后端是 `59134`。
- `frontend/.env` 中的 `VITE_API_BASE` 不会影响 Vite proxy；必须统一使用 `VITE_API_PROXY_TARGET`。
- Events 搜索 debounce 已生效，快速输入只触发 1 次查询。
- 初始 JS gzip 约 365.0 KB，总 JS gzip 约 734.2 KB。
- Login 不包含 charts；`charts` chunk 约 347.6 KB gzip，非初始资源。
- 初始包主要由 Ant Design vendor 主导，约 297.9 KB gzip。
- Events 首屏 3 个业务 API，其中 facets 可延后到筛选抽屉打开时加载。

前端修复契约：

- 开发代理由 `VITE_API_PROXY_TARGET` 控制，不写死临时端口。
- AI 配置保存成功后立即更新配置缓存，Key 变化时清理模型列表缓存。
- 401 不重试，直接进入登录流程。
- 事件页可见时按 10 秒刷新；页面隐藏时停止非关键刷新。
- facets 延迟到高级筛选抽屉打开时加载。
- DB commit 后事件页面目标 10 秒内可见。
