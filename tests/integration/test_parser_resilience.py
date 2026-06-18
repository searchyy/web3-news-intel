from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from app.adapters.html import HTMLAdapter
from app.adapters.json_api import JSONAPIAdapter
from app.adapters.rss import RSSAdapter
from app.core.config import SourceConfig
from app.schemas.raw_document import RawDocumentPayload


def _source(**overrides) -> SourceConfig:
    data = {
        "key": "fixture",
        "name": "Fixture",
        "source_type": "tier1_media",
        "adapter": "rss",
        "url": "https://example.com/feed.xml",
        "canonical_url": "https://example.com/feed.xml",
        "category": "media",
        "language": "en",
        "trust_score": 75,
        "poll_seconds": 300,
        "timeout_seconds": 15,
        "max_response_bytes": 2097152,
        "enabled": True,
        "config": {"parser_version": "generic_rss_v1"},
    }
    data.update(overrides)
    return SourceConfig(**data)


def _raw(source: SourceConfig, fixture: str) -> RawDocumentPayload:
    body = Path(f"tests/fixtures/{fixture}").read_text(encoding="utf-8")
    return RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        body_hash=sha256(body.encode("utf-8")).hexdigest(),
        body=body,
    )


async def test_enabled_rss_source_fixtures_parse() -> None:
    fixtures = [
        ("sec_press", "regulator_official", "regulation", "rss_sec.xml"),
        ("cftc_press", "regulator_official", "regulation", "rss_cftc.xml"),
        ("ethereum_blog", "protocol_official", "protocol", "rss_ethereum.xml"),
        ("coindesk", "tier1_media", "media", "rss_coindesk.xml"),
    ]
    for key, source_type, category, fixture in fixtures:
        source = _source(key=key, source_type=source_type, category=category)
        items = await RSSAdapter().parse(source, _raw(source, fixture))
        assert items
        assert items[0].raw["parser_version"] == "generic_rss_v1"


async def test_defillama_json_fixture_parses() -> None:
    source = _source(
        key="defillama_hacks",
        source_type="onchain_data",
        adapter="json_api",
        url="https://api.llama.fi/hacks",
        canonical_url="https://api.llama.fi/hacks",
        category="security",
        trust_score=85,
        config={
            "parser_version": "defillama_hacks_json_v1",
            "items_path": "hacks",
            "title_fields": ["name", "title"],
            "summary_fields": ["description", "classification"],
            "url_fields": ["url"],
            "date_fields": ["date"],
        },
    )
    items = await JSONAPIAdapter().parse(source, _raw(source, "json_defillama_hacks.json"))
    assert len(items) == 1
    assert items[0].category == "security"
    assert items[0].raw["parser_version"] == "defillama_hacks_json_v1"


async def test_empty_feed_returns_no_items() -> None:
    source = _source()
    assert await RSSAdapter().parse(source, _raw(source, "rss_empty.xml")) == []


async def test_missing_optional_fields_are_tolerated() -> None:
    source = _source()
    items = await RSSAdapter().parse(source, _raw(source, "rss_missing_optional_fields.xml"))
    assert len(items) == 1
    assert items[0].summary is None
    assert items[0].published_at is None


async def test_malformed_publication_date_is_tolerated() -> None:
    source = _source()
    items = await RSSAdapter().parse(source, _raw(source, "rss_malformed_dates.xml"))
    assert len(items) == 1
    assert items[0].published_at is None


async def test_duplicate_entries_and_changed_order_parse_deterministically() -> None:
    source = _source()
    items = await RSSAdapter().parse(source, _raw(source, "rss_sec.xml"))
    reversed_titles = [item.title for item in reversed(items)]
    assert len(items) == 2
    assert reversed_titles == [items[1].title, items[0].title]


async def test_html_missing_selectors_returns_no_items() -> None:
    source = _source(
        adapter="html",
        url="https://example.com/listings",
        canonical_url="https://example.com/listings",
        category="listing",
        source_type="exchange_official",
        config={
            "parser": "fixture",
            "parser_version": "generic_html_v1",
            "item_selector": ".announcement",
            "title_selector": ".title",
            "url_selector": ".title",
        },
    )
    raw = RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        body_hash=sha256(b"<html><body><p>changed shape</p></body></html>").hexdigest(),
        body="<html><body><p>changed shape</p></body></html>",
    )
    assert await HTMLAdapter().parse(source, raw) == []
