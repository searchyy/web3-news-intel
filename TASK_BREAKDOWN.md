# Task Breakdown for Codex

## Milestone 0 — Repo Bootstrap

Acceptance:
- Project installs with `pip install -e ".[dev]"`
- `pytest` runs
- `ruff` and `mypy` can run
- `docker compose config` succeeds

## Milestone 1 — Core API + Config

Tasks:
- Create FastAPI app
- Add settings from env
- Add `/health`
- Add structured logs

Acceptance:
- `GET /health` returns `{"status":"ok"}`

## Milestone 2 — Database

Tasks:
- Create models and migrations
- Create repositories
- Add seed script for sources

Acceptance:
- `alembic upgrade head`
- Source upsert is idempotent
- Event upsert is idempotent

## Milestone 3 — Fetcher

Tasks:
- Implement robots cache
- Implement rate limiter
- Implement retry policy
- Implement FetchClient

Acceptance:
- rate-limit responses wait according to publisher backoff headers
- `500` retries with backoff
- terminal access responses raise AccessDenied
- disallowed robots path is skipped

## Milestone 4 — Source Adapters

Tasks:
- RSS adapter
- JSON adapter
- GraphQL adapter
- HTML adapter
- Registry

Acceptance:
- RSS fixture returns normalized items
- HTML fixture returns normalized items
- adapter errors create failed fetch_runs

## Milestone 5 — Event Pipeline

Tasks:
- Normalizer
- Entity/symbol extraction
- Dedupe
- Scoring
- Alert decisions

Acceptance:
- Similar listing titles cluster into one event
- Official source creates confirmed event
- Media-only sensitive report becomes needs_review
- Same URL does not duplicate

## Milestone 6 — Publishers

Tasks:
- Telegram
- Discord
- Webhook
- Delivery table

Acceptance:
- Same event/channel cannot be delivered twice
- Failed delivery can retry
- Message body includes title, category, score, source URL

## Milestone 7 — Admin/API

Tasks:
- List events
- Filter events
- List sources
- Reload sources
- Republish event

Acceptance:
- API tests pass
- Filters work

## Milestone 8 — Local Production Readiness

Tasks:
- Docker Compose
- README
- .env.example
- Metrics
- Logging docs
- Operational runbook

Acceptance:
- New developer can run locally in under 10 minutes
- README documents source addition
- README documents compliance boundaries
