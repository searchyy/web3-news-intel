# Data Model

## Tables

### sources

```sql
CREATE TABLE sources (
  id BIGSERIAL PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  adapter TEXT NOT NULL,
  url TEXT NOT NULL,
  category TEXT NOT NULL,
  language TEXT,
  trust_score INTEGER NOT NULL DEFAULT 50,
  poll_seconds INTEGER NOT NULL DEFAULT 300,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### fetch_runs

```sql
CREATE TABLE fetch_runs (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id),
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  http_status INTEGER,
  item_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  trace_id TEXT NOT NULL
);
```

### raw_documents

```sql
CREATE TABLE raw_documents (
  id BIGSERIAL PRIMARY KEY,
  source_id BIGINT NOT NULL REFERENCES sources(id),
  fetch_run_id BIGINT REFERENCES fetch_runs(id),
  url TEXT NOT NULL,
  canonical_url TEXT,
  content_type TEXT,
  status_code INTEGER,
  body_hash TEXT NOT NULL,
  body TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_id, body_hash)
);
```

### events

```sql
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  event_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  summary TEXT,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'normal',
  language TEXT,
  primary_url TEXT,
  published_at TIMESTAMPTZ,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  trust_score INTEGER NOT NULL DEFAULT 50,
  confirmation_count INTEGER NOT NULL DEFAULT 1,
  symbols TEXT[] NOT NULL DEFAULT '{}',
  chains TEXT[] NOT NULL DEFAULT '{}',
  entities TEXT[] NOT NULL DEFAULT '{}',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
```

### event_sources

```sql
CREATE TABLE event_sources (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id),
  source_id BIGINT NOT NULL REFERENCES sources(id),
  raw_document_id BIGINT REFERENCES raw_documents(id),
  url TEXT NOT NULL,
  title TEXT,
  published_at TIMESTAMPTZ,
  source_score INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(event_id, source_id, url)
);
```

### deliveries

```sql
CREATE TABLE deliveries (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id),
  channel TEXT NOT NULL,
  target TEXT NOT NULL,
  status TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Event Status

- `confirmed`: official source or strong cross-source confirmation
- `needs_review`: media-only, low confidence, or sensitive category
- `draft`: parsed but incomplete
- `rejected`: duplicate/spam/invalid

## Severity

- `critical`: exploit, exchange halt, regulator enforcement, stablecoin depeg, major chain halt
- `high`: listing/delisting, large protocol incident, major governance approval
- `normal`: funding, partnership, routine upgrade
- `low`: minor media report, commentary
