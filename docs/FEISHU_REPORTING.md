# 飞书汇报

本版本保留现有即时飞书告警，并新增“汇报”能力，用于把一个时间窗口内的事件汇总成一张飞书交互卡片。

## 汇报类型

- 立即告警：沿用现有即时投递链路。
- 15 分钟摘要：`report_type=digest_15m`。
- 30 分钟摘要：`report_type=digest_30m`。
- 每小时汇报：`report_type=hourly`。
- 每日早报：`report_type=daily_morning`。
- 每日晚报：`report_type=daily_evening`。
- 自定义时间：`report_type=custom`，使用受控 `hour`、`minute` 和 `timezone` 配置。

## 数据模型

`report_schedules` 保存汇报规则：

- `destination_id`：飞书群或飞书 webhook 目的地。
- `name`、`enabled`、`report_type`、`timezone`、`interval_minutes`、`hour`、`minute`。
- `saved_search_id`：可复用保存的事件筛选条件。
- `source_groups`、`categories`、`severities`、`symbols`、`chains`、`minimum_trust_score`。
- `include_ai_summary`、`maximum_events`。
- `activated_at`、`last_window_start`、`last_window_end`、`last_run_at`、`next_run_at`。

## 卡片内容

飞书汇报卡片包含：

- 汇报周期。
- 事件总数。
- critical/high 数量。
- 主要币种和主要分类。
- Top 事件列表。
- AI 中文摘要；如果 AI 不可用或事件没有 AI 结果，则使用确定性模板摘要。
- 事件原文链接。
- 管理面板“查看全部事件”按钮。

每张卡最多展示 10 条事件，超出部分显示“还有 N 条事件未在卡片中展示”。

## 幂等与历史事件保护

- 新群或新规则启用时设置 `activated_at`，首次窗口不发送启用前的历史事件。
- 汇报投递使用窗口幂等键，包含目的地、汇报类型、筛选条件、窗口起止时间。
- Celery retry 或同一 schedule 重复触发不会重复投递。
- 两条配置完全相同的汇报规则在同一群、同一窗口内共享同一条 delivery。
- 无事件窗口默认不发送空卡片。

## 安全默认值

- `FEISHU_SEND_ENABLED=false` 时只记录 dry-run delivery，不调用真实飞书。
- CI 和 mock E2E 使用 mock Feishu。
- 手动 `test-send` 只发送一张测试汇报卡片。
- 卡片 URL 必须是公开 HTTP(S) URL，私网、localhost、link-local 地址不会进入卡片。
- 汇报不会读取或输出飞书密钥、webhook 明文、Cookie、Header 或内部日志。

## 管理 API

- `GET /api/admin/report-schedules`
- `POST /api/admin/report-schedules`
- `GET /api/admin/report-schedules/{id}`
- `PATCH /api/admin/report-schedules/{id}`
- `DELETE /api/admin/report-schedules/{id}`
- `POST /api/admin/report-schedules/{id}/preview`
- `POST /api/admin/report-schedules/{id}/run`
- `POST /api/admin/report-schedules/{id}/test-send`

所有写操作需要 Admin Session 和 CSRF。

## 验收状态

本机已通过飞书汇报单元测试和 fixture 集成测试。真实飞书发送未执行，最终报告标记为 `NOT EXECUTED`。
