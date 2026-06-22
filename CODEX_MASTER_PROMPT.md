# Codex Master Prompt

You are building a production-grade Python project named `web3-news-intel`.

Goal:
Build a reliable, modular Web3/Crypto news intelligence crawler that collects official announcements, regulatory updates, exchange listings/delistings, protocol releases, governance proposals, on-chain/security signals, and selected media/newsflash sources. The system must normalize, deduplicate, score trust, cluster multiple reports into events, and publish alerts via Telegram/Discord/Webhook while exposing a FastAPI API.

Hard constraints:
1. Collect only public or explicitly approved sources and obey source-owner terms.
2. Respect robots.txt for HTML sources by default.
3. Respect publisher rate limits and backoff headers.
4. Treat terminal access responses as non-retriable and record the source/job outcome.
5. Store only title, summary, metadata, source URL, and short snippets where allowed. Do not bulk-copy full copyrighted articles unless the source license/API permits it.
6. All timestamps must be timezone-aware UTC.
7. All external calls must be testable with mocks.
8. Every module must have unit tests.
9. Provide Docker Compose for local development.

Use:
- Python 3.12
- FastAPI
- PostgreSQL
- Redis
- Celery or Dramatiq
- SQLAlchemy 2.x
- Alembic
- Pydantic v2
- httpx
- feedparser
- BeautifulSoup/lxml
- structlog
- pytest
- respx

Implement in phases. After each phase, run tests and produce a concise summary of what changed.

Phase 1: Project skeleton
- Create pyproject.toml
- Create app package structure
- Add config loading from env
- Add structured logging
- Add Docker Compose with postgres/redis/api/worker/scheduler
- Add health endpoint
- Add pytest setup

Phase 2: Database
- Implement SQLAlchemy models:
  - sources
  - fetch_runs
  - raw_documents
  - events
  - event_sources
  - deliveries
- Add Alembic migrations
- Add repositories with idempotent upsert methods
- Add tests for unique constraints and upsert behavior

Phase 3: Fetch layer
- Implement FetchClient:
  - httpx async client
  - timeout
  - max response size
  - per-host rate limit
  - robots.txt check
  - retry/backoff
  - publisher backoff headers
  - terminal access response handling
- Add tests using respx

Phase 4: Adapters
- Implement RSS adapter using feedparser
- Implement JSON API adapter
- Implement GraphQL adapter
- Implement generic HTML adapter
- Add adapter registry
- Add tests using fixtures

Phase 5: Pipeline
- Implement normalize:
  - canonical URL
  - clean title
  - parse published_at
  - language detection simple heuristic
  - symbol extraction
  - chain/entity extraction
- Implement dedupe service
- Implement event clustering
- Add tests

Phase 6: Scoring and alert rules
- Implement source trust scoring
- Implement confirmation_count
- Implement event status:
  - confirmed
  - needs_review
  - draft
  - rejected
- Implement severity:
  - critical
  - high
  - normal
  - low
- Implement AlertEngine
- Add tests

Phase 7: Publishers
- Implement Telegram publisher
- Implement Discord publisher
- Implement generic webhook publisher
- Implement idempotent deliveries
- Add retry-safe tests

Phase 8: API
- Implement:
  - GET /health
  - GET /events
  - GET /events/{id}
  - GET /sources
  - POST /admin/sources/reload
  - POST /admin/events/{id}/republish
  - GET /metrics
- Add filtering by category, symbol, source, status, severity, time range
- Add tests

Phase 9: Source config
- Load sources from sources.yaml
- Validate with Pydantic
- Seed database sources from YAML
- Include example sources from SOURCES.example.yaml

Phase 10: End-to-end
- Implement scheduler that enqueues due sources
- Implement worker tasks:
  - fetch_source
  - parse_raw_document
  - score_event
  - publish_event
- Add local e2e test that uses fixture RSS and verifies event + delivery creation

Definition of done:
- `docker compose up` starts all services
- `pytest` passes
- `alembic upgrade head` works
- `/health` returns ok
- sample RSS fixture produces events
- duplicate feed items do not create duplicate events
- rate-limit responses use publisher backoff headers
- terminal access responses are recorded without repeated retries
- delivery is idempotent
- README explains setup, source addition, operations, and compliance boundaries
