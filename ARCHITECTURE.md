# Architecture

## 1. System Overview

```text
                 ┌──────────────────────┐
                 │ sources.yaml          │
                 │ RSS/API/HTML/GQL      │
                 └──────────┬───────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────┐
│ Scheduler                                            │
│ - reads source configs                               │
│ - creates fetch jobs                                 │
│ - respects poll intervals                            │
└───────────────────────┬─────────────────────────────┘
                        │ Redis queue
                        ▼
┌─────────────────────────────────────────────────────┐
│ Fetch Worker                                         │
│ - rate limit per host                                │
│ - robots.txt check                                   │
│ - retry with exponential backoff                     │
│ - Retry-After support                                │
│ - access-denied stop policy                          │
└───────────────────────┬─────────────────────────────┘
                        │ RawDocument
                        ▼
┌─────────────────────────────────────────────────────┐
│ Parser / Adapter Layer                               │
│ - RSS adapter                                        │
│ - JSON API adapter                                   │
│ - GraphQL adapter                                    │
│ - HTML adapter                                       │
│ - optional browser-render adapter                    │
└───────────────────────┬─────────────────────────────┘
                        │ NormalizedItem
                        ▼
┌─────────────────────────────────────────────────────┐
│ Normalize + Entity Extract                           │
│ - canonical URL                                      │
│ - title normalization                                │
│ - symbol/project extraction                          │
│ - category classification                            │
│ - language detection                                 │
└───────────────────────┬─────────────────────────────┘
                        │ CandidateEvent
                        ▼
┌─────────────────────────────────────────────────────┐
│ Dedup + Event Clustering                             │
│ - source_url hash                                    │
│ - content hash                                       │
│ - title similarity                                   │
│ - symbol + category + time window                    │
└───────────────────────┬─────────────────────────────┘
                        │ Event
                        ▼
┌─────────────────────────────────────────────────────┐
│ Trust Scoring + Confirmation                         │
│ - official source score                              │
│ - media source score                                 │
│ - cross-source confirmation                          │
│ - severity rules                                     │
└───────────────────────┬─────────────────────────────┘
                        │ AlertDecision
                        ▼
┌─────────────────────────────────────────────────────┐
│ Publisher                                            │
│ - Telegram                                           │
│ - Discord                                            │
│ - Slack                                              │
│ - webhook                                            │
│ - email                                              │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│ FastAPI                                              │
│ - /events                                            │
│ - /sources                                           │
│ - /health                                            │
│ - /metrics                                           │
│ - /admin/retry                                       │
└─────────────────────────────────────────────────────┘
```

## 2. Deployment Topology

```text
docker-compose
  postgres
  redis
  api
  scheduler
  worker_fetch
  worker_parse
  worker_publish
  prometheus optional
```

MVP 可以把 `worker_fetch`、`worker_parse`、`worker_publish` 合并成一个 worker。生产版建议拆分。

## 3. Data Flow

1. Scheduler 扫描 `sources.yaml`。
2. 对每个到达轮询时间的 source 创建 `FetchJob`。
3. Fetcher 根据 adapter 类型抓取 RSS/API/HTML/GraphQL。
4. Raw response 存入 `raw_documents`。
5. Parser 输出多个 `NormalizedItem`。
6. Normalizer 提取 symbol、project、chain、category、published_at。
7. Dedupe 把同一事件合并到 `events`。
8. Scorer 计算 `trust_score`、`severity`、`confirmation_count`。
9. Alert engine 判断是否推送。
10. Publisher 发送 Telegram/Discord/Webhook，并记录 delivery 状态。

## 4. Reliability Design

- 每个 source 独立 poll interval。
- 每个 host 独立 rate limit。
- `429` 尊重 `Retry-After`。
- `5xx` 指数退避。
- `401/403` 标记 `ACCESS_DENIED`，不无限重试。
- 每个 job 有 idempotency key。
- 原始响应可回放解析。
- 推送失败可重试，避免重复推送。
- 数据库唯一约束保证去重。
- 所有任务带 trace id。

## 5. Compliance Boundary

System must not implement anti-bot bypass. In particular:

- no CAPTCHA solving
- no Cloudflare challenge bypass
- no stealth browser fingerprinting
- no residential proxy rotation to bypass rate limits
- no cookie/session reuse from unauthorized accounts
- no scraping of private/paywalled/login-only content

Allowed:

- official API integration
- RSS polling
- robots-aware public HTML fetching
- reasonable rate limiting
- authorized fixed egress IP
- site-owner allowlist
- manual token configuration for accounts the user owns or is authorized to use
