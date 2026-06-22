# Operations Runbook

## Local Start

```bash
cp .env.example .env
docker compose up --build
```

## Database

```bash
docker compose exec api alembic upgrade head
docker compose exec api python scripts/init_db.py --sources sources.example.yaml
```

## Run One Source Manually

```bash
docker compose exec worker python scripts/backfill_source.py --source sec_press --limit 1
```

## Replay Raw Document

```bash
docker compose exec worker python scripts/replay_raw_document.py --raw-document-id 123
```

## Health Checks

- `/health`
- `/metrics`
- worker heartbeat
- scheduler heartbeat
- Redis queue length
- fetch error rate
- access and rate-limit error rate
- event creation rate
- delivery failure rate

## Alert Fatigue Controls

- max alerts per source per hour
- max alerts per symbol per hour
- suppress duplicate titles
- critical severity overrides most throttles
- media-only rumors go to review channel

## Common Failure Modes

### Rate Limited

Action:
- respect publisher-provided backoff headers
- reduce poll interval
- lower per-host concurrency

### Access Blocked

Action:
- disable source
- use official API
- request authorization

### Parser Breakage

Action:
- inspect raw_documents
- add fixture from failed page
- update parser
- replay raw document
