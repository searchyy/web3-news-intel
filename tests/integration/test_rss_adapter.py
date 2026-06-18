from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from app.adapters.rss import RSSAdapter
from app.core.config import SourceConfig
from app.schemas.raw_document import RawDocumentPayload


async def test_rss_fixture_parses_items() -> None:
    body = Path("tests/fixtures/rss_coindesk.xml").read_text(encoding="utf-8")
    source = SourceConfig(
        key="coindesk",
        name="CoinDesk",
        source_type="tier1_media",
        adapter="rss",
        url="https://coindesk.example/rss",
        canonical_url="https://coindesk.example/rss",
        category="media",
        trust_score=75,
        timeout_seconds=15,
        max_response_bytes=2097152,
    )
    raw = RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        body_hash=sha256(body.encode()).hexdigest(),
        body=body,
    )
    items = await RSSAdapter().parse(source, raw)
    assert len(items) == 1
    assert items[0].title == "Protocol ABC Releases Mainnet Upgrade"
    assert items[0].published_at is not None
    assert items[0].published_at.tzinfo is not None
