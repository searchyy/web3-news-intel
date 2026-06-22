# web3-news-intel

Reliable Web3/Crypto news intelligence crawler and alerting service. It collects public official announcements, regulatory updates, protocol releases, governance proposals, on-chain/security signals, and selected media feeds, then normalizes, deduplicates, clusters, scores, and publishes alerts.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests/unit tests/integration
```

PostgreSQL and Redis integration tests are skipped unless `TEST_DATABASE_URL`, `TEST_REDIS_URL`, and `RUN_CELERY_WORKER_TESTS=1` are configured.

## Docker Setup

```bash
cp .env.example .env
docker compose config --quiet
docker compose build
docker compose up -d
curl --fail http://127.0.0.1:18080/health
docker compose ps
docker compose logs --no-color
docker compose down -v --remove-orphans
```

Compose includes health checks for PostgreSQL (`pg_isready`), Redis (`redis-cli ping`), FastAPI (`/health`), Celery worker heartbeat, and scheduler startup. API waits for PostgreSQL and Redis. Worker and scheduler wait for healthy PostgreSQL, Redis, and API. The host API port is controlled by `APP_PORT` and defaults to `18080`.

The visual administration frontend is served by the `frontend` Compose service. The host frontend port is controlled by `FRONTEND_PORT` and defaults to `18081`.

## Database Migration

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

The production schema targets PostgreSQL and uses `JSONB`, `TEXT[]`, and timezone-aware timestamp columns.

## Adding a Source

Add entries to `sources.yaml`:

```yaml
sources:
  project_blog:
    name: "Project Blog"
    source_type: "protocol_official"
    adapter: "rss"
    url: "https://example.org/feed.xml"
    canonical_url: "https://example.org/feed.xml"
    category: "protocol"
    language: "en"
    trust_score: 95
    poll_seconds: 300
    timeout_seconds: 15
    max_response_bytes: 2097152
    enabled: true
    allow_private_networks: false
    allow_localhost: false
    config:
      parser_version: "generic_rss_v1"
```

Validate:

```bash
python scripts/validate_sources.py sources.yaml
```

URL safety checks are enabled by default. Test fixtures must explicitly opt in through test-only settings or fixture-only source configuration.

## Running One Source

```bash
python scripts/backfill_source.py sec_press
```

This uses the same fetch, parse, dedupe, and scoring path as the worker.

## Replaying Raw Documents

```bash
python scripts/replay_raw_document.py 123
```

Replay is intended for parser changes. Raw documents retain source, URL, content type, body hash, metadata, and fetch time.

## Alert Publishers

Set one or more:

```env
ALERT_WEBHOOK_URL=https://example.com/webhook
ALERT_WEBHOOK_SECRET=change-me
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Webhook deliveries include `X-Webhook-Signature` when `ALERT_WEBHOOK_SECRET` is set. Delivery records are idempotent by event/channel/target.

## Feishu Integration

Feishu enterprise application bot mode is the production mode. Custom webhook mode is outbound-only compatibility mode.

Safe defaults:

- `FEISHU_ENABLED=false`
- `FEISHU_SEND_ENABLED=false`
- webhook URLs are encrypted with `FIELD_ENCRYPTION_KEY`
- app secrets remain in environment variables or a secret manager
- newly enabled groups do not receive historical events automatically

See [docs/FEISHU_SETUP.md](docs/FEISHU_SETUP.md).

## Admin Authentication

Legacy `/admin/*` routes require `X-Admin-Token` and a configured `ADMIN_TOKEN`. Token comparison uses constant-time comparison. If `ADMIN_TOKEN` is unset, admin routes return `503`.

The visual administration panel uses `/api/admin/auth/login`, Argon2 password verification, server-side sessions, HttpOnly cookies, and CSRF protection. Browser localStorage is not used for admin tokens. See [docs/ADMIN_PANEL.md](docs/ADMIN_PANEL.md).

## Troubleshooting

- Terminal access responses mark the source as unavailable.
- Rate-limited responses honor publisher-provided backoff headers.
- HTML sources respect `robots.txt`.
- Dynamic HTML exchange and Chinese media sources are disabled until parser selectors are verified.
- `/metrics` exposes bounded-cardinality Prometheus metrics; it does not label by full URL, title, event ID, or symbol.

## Backup And Restore

PostgreSQL backup:

```bash
pg_dump "$DATABASE_URL" > web3_news_intel.sql
```

Restore into an empty database:

```bash
psql "$DATABASE_URL" < web3_news_intel.sql
alembic upgrade head
```

Back up `sources.yaml`, `.env` secrets in your secret manager, and any retained raw document storage.

## Compliance Boundaries

This project is scoped to public or explicitly approved sources.

External calls are testable with mocks. Fetching enforces timeout, response-size limits, per-host rate limiting, safe redirects, and URL safety checks. Retained content should be limited to metadata, titles, summaries, source URLs, and permitted snippets.

## Production Deployment Caveats

Run the `quality`, `postgres-integration`, `redis-celery-integration`, and `compose-acceptance` CI jobs before release. Configure `ADMIN_TOKEN`, publisher secrets, backups, log retention, alert routing, infrastructure-level trusted proxy settings, and production URL safety defaults. The separate live-source canary is scheduled and non-blocking for pull requests.
