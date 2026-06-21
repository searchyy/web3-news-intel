# 事件搜索与高级筛选

本版本把管理后台事件列表改为服务端搜索和分页，避免前端一次性拉取全部事件再筛选。默认搜索范围覆盖规范化事件字段、来源字段和 AI 结构化结果，不把 raw HTML 或完整受版权保护正文加入默认索引。

## API

`GET /api/admin/events`

支持参数：

- `q`：关键词。
- `q_mode`：`all`、`any`、`phrase`。
- `source_keys`、`source_groups`、`categories`、`severities`、`statuses`。
- `symbols`、`chains`、`languages`。
- `official_only`、`minimum_trust_score`、`has_ai_summary`。
- `published_from`、`published_to`、`first_seen_from`、`first_seen_to`。
- `sort`：仅允许 `published_at`、`first_seen_at`、`last_seen_at`、`trust_score`、`severity`、`confirmation_count`、`id`。
- `direction`：`asc` 或 `desc`。
- `page`、`page_size`。

兼容旧参数：`limit`、`offset`、`category`、`severity`、`status`、`published_at_desc`、`first_seen_at_desc`、`severity_desc`。

`GET /api/admin/events/facets`

返回当前筛选条件下的聚合项：分类、级别、状态、语言、来源、来源组、币种和链。

保存搜索：

- `POST /api/admin/saved-searches`
- `GET /api/admin/saved-searches`
- `PATCH /api/admin/saved-searches/{id}`
- `DELETE /api/admin/saved-searches/{id}`

所有写操作都要求 Admin Session 和 CSRF。

## 搜索范围

- `events.title`
- `events.summary`
- 前端展示标题和摘要对应的后端规范化字段
- `events.symbols`
- `events.chains`
- `events.entities`
- `sources.name`
- `sources.key`
- 如果 `event_ai_insights` 表存在，附加搜索 `headline_zh`、`summary_zh`、`key_facts`、`entities`

`display_title` 和 `display_summary` 当前由 schema 从 `title`、`summary` 派生，因此后端搜索等价覆盖原始规范化字段。

## 安全设计

- 所有筛选条件通过 SQLAlchemy 表达式和绑定参数传入。
- `LIKE` 对 `%`、`_` 和反斜杠做转义。
- 排序字段走固定 allowlist，不接受任意 SQL 片段。
- 中文使用子串匹配，英文大小写不敏感，币种符号按数组字段过滤。
- 搜索不读取 raw document body，不索引 raw HTML。

## PostgreSQL 索引

迁移 `0004_event_search`：

- 启用 `pg_trgm`。
- `lower(coalesce(title, ''))` GIN trigram。
- `lower(coalesce(summary, ''))` GIN trigram。
- `symbols`、`chains`、`entities` GIN。
- `first_seen_at`、`last_seen_at`、`trust_score` B-tree。
- `status,severity,first_seen_at` 组合索引。
- `category,first_seen_at` 组合索引。
- `event_sources(source_id,event_id)` 组合索引。
- 新增 `saved_searches` 表和 owner/update 索引。

`symbols` 和 `chains` 的 facets 在 PostgreSQL 下使用 `unnest` 在数据库侧聚合，避免把当前结果集全部拉到 Python 展开；SQLite 单元测试仍保留 JSON 文本 fallback。

## 10,000 事件性能验收

集成测试入口：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_event_search_postgres.py -q
```

测试会在真实 PostgreSQL 中生成：

- 10,000 个 events。
- 20 个 sources。
- 10,000 条 event_sources。
- 2,000 条 successful AI insights。

覆盖查询：

- 中文短语搜索。
- 英文大小写不敏感搜索。
- AND/OR 关键词。
- symbol、chain、source group、official only。
- trust score、AI summary、日期范围。
- 服务端分页和稳定排序。
- SQL 注入输入。
- facets 数据库侧聚合。

性能门禁：

- 常用查询整体 p95 必须低于 500ms。
- 代表性查询必须通过 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` 证明使用 PostgreSQL 索引。

CI artifact：

- `artifacts/search-performance.json`
- `artifacts/search-performance.md`

本地如果没有真实 PostgreSQL，不得把该测试报告为通过；SQLite 不能替代 PostgreSQL 性能验收。
