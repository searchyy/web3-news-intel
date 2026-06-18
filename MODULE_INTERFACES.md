# Module Interfaces

Codex should implement these contracts first, then fill adapters/parsers.

## Adapter Base

```python
from typing import Protocol
from app.schemas.normalized_item import NormalizedItem

class Adapter(Protocol):
    async def fetch(self, source: SourceConfig) -> list[RawDocument]:
        ...

    async def parse(self, source: SourceConfig, raw: RawDocument) -> list[NormalizedItem]:
        ...
```

## NormalizedItem

```python
class NormalizedItem(BaseModel):
    title: str
    summary: str | None = None
    url: str
    canonical_url: str | None = None
    published_at: datetime | None = None
    source_key: str
    source_type: str
    category: str
    language: str | None = None
    symbols: list[str] = []
    chains: list[str] = []
    entities: list[str] = []
    raw: dict = {}
```

## FetchClient

```python
class FetchClient:
    async def get_text(self, url: str, *, headers: dict | None = None) -> FetchResponse:
        ...

    async def post_json(self, url: str, *, json: dict, headers: dict | None = None) -> FetchResponse:
        ...
```

Required behavior:

- default timeout
- max response size
- per-host rate limit
- robots check for HTML pages
- Retry-After support
- exponential backoff for transient errors
- stop on 401/403
- structured logs

## Dedupe

```python
class DedupeService:
    def build_event_key(self, item: NormalizedItem) -> str:
        ...

    async def upsert_event(self, item: NormalizedItem) -> Event:
        ...
```

Dedupe dimensions:

- canonical_url
- normalized title
- source category
- symbols
- published_at time bucket
- project/entity names

## Scoring

```python
class ScoringService:
    def score(self, event: Event, event_sources: list[EventSource]) -> ScoreResult:
        ...
```

Rules:

- official regulator/exchange/protocol: confirmed directly
- media-only sensitive events: needs_review until second source
- onchain data can increase confidence but must be labeled as inference
- high-impact categories should be pushed faster but with careful wording

## Alert Engine

```python
class AlertEngine:
    def should_alert(self, event: Event) -> AlertDecision:
        ...
```

Immediate alert categories:

- listing
- delisting
- enforcement
- exploit
- depeg
- chain_halt
- protocol_upgrade
- governance_passed

Review-required categories:

- rumor
- media-only exploit claim
- anonymous social claim
- price prediction
