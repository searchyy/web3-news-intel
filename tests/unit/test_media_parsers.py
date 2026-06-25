from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from app.adapters.media.html import MediaHTMLAdapter
from app.adapters.media.json_api import MediaJSONAPIAdapter
from app.adapters.media.rss import MediaRSSAdapter
from app.core.config import SourceConfig
from app.schemas.raw_document import RawDocumentPayload


def _source(**overrides: object) -> SourceConfig:
    data = {
        "key": "fixture_media",
        "name": "Fixture Media",
        "source_type": "tier1_media",
        "adapter": "rss",
        "url": "https://example.com/feed.xml",
        "canonical_url": "https://example.com/feed.xml",
        "category": "market",
        "language": "en",
        "trust_score": 65,
        "poll_seconds": 300,
        "timeout_seconds": 15,
        "max_response_bytes": 2097152,
        "enabled": True,
        "config": {"source_group": "media_en", "parser_version": "media_rss_v1"},
    }
    data.update(overrides)
    return SourceConfig(**data)


def _raw(source: SourceConfig, fixture: str) -> RawDocumentPayload:
    body = Path(f"tests/fixtures/media/{fixture}").read_text(encoding="utf-8")
    return RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        canonical_url=source.canonical_url,
        body_hash=sha256(body.encode("utf-8")).hexdigest(),
        body=body,
    )


async def test_media_rss_parser_keeps_allowed_fields_only() -> None:
    source = _source(key="coindesk_rss", name="CoinDesk", category="market")
    items = await MediaRSSAdapter().parse(source, _raw(source, "rss_coindesk_media.xml"))

    assert len(items) == 1
    item = items[0]
    assert item.category == "fundraising"
    assert item.raw["official_confirmation"] is False
    assert item.raw["requires_multisource_confirmation"] is True
    assert item.raw["copyright_scope"] == "metadata_summary_link_only"
    assert item.raw["article_body_saved"] is False
    assert "Ethereum" in item.raw["tags"]
    assert "long article body" not in item.summary
    assert "long article body" not in str(item.raw)


async def test_media_rss_parser_classifies_security_and_market() -> None:
    decrypt = _source(
        key="decrypt_rss",
        name="Decrypt",
        url="https://decrypt.co/feed",
        canonical_url="https://decrypt.co/feed",
    )
    security_items = await MediaRSSAdapter().parse(decrypt, _raw(decrypt, "rss_decrypt_media.xml"))
    assert security_items[0].category == "hack_security"
    assert "ExampleFi" in security_items[0].raw["cluster_hint"]["title_fingerprint_basis"]

    cointelegraph = _source(
        key="cointelegraph_rss",
        name="Cointelegraph",
        url="https://cointelegraph.com/rss",
        canonical_url="https://cointelegraph.com/rss",
    )
    market_items = await MediaRSSAdapter().parse(
        cointelegraph, _raw(cointelegraph, "rss_cointelegraph_media.xml")
    )
    assert market_items[0].category == "market"
    assert "BTC" in market_items[0].symbols


async def test_project_official_rss_marks_official_confirmation() -> None:
    source = _source(
        key="aster_medium",
        name="Aster Medium",
        source_type="project_official",
        adapter="media_rss",
        url="https://medium.com/feed/asterdex",
        canonical_url="https://medium.com/asterdex",
        category="project_update",
        language="en",
        config={
            "source_group": "project_official",
            "official": True,
            "parser_version": "aster_medium_rss_v1",
        },
    )

    items = await MediaRSSAdapter().parse(source, _raw(source, "rss_aster_medium.xml"))

    assert len(items) == 1
    assert items[0].raw["official_confirmation"] is True
    assert items[0].raw["requires_multisource_confirmation"] is False
    assert "ASTER" in items[0].symbols
    assert "DEX" not in items[0].symbols
async def test_media_html_parser_handles_blockbeats_newsflash_listing() -> None:
    source = _source(
        key="blockbeats_newsflash",
        name="BlockBeats Newsflash",
        source_type="chinese_media",
        adapter="html",
        url="https://m.theblockbeats.info/newsflash",
        canonical_url="https://m.theblockbeats.info/newsflash",
        category="newsflash",
        language="zh",
        config={
            "source_group": "media_zh",
            "parser": "media_html",
            "parser_version": "blockbeats_newsflash_html_v1",
            "item_selector": "a.newsflash-card",
            "title_selector": ".title",
            "url_selector": "",
            "summary_selector": ".summary",
            "date_selector": ".time",
            "tag_selector": ".tag",
            "max_items": 10,
        },
    )
    items = await MediaHTMLAdapter().parse(source, _raw(source, "html_blockbeats_newsflash.html"))

    assert len(items) == 2
    assert items[0].language == "zh"
    assert items[0].category == "newsflash"
    assert items[0].url == "https://m.theblockbeats.info/flash/123"
    assert items[0].raw["source_group"] == "media_zh"
    assert items[0].raw["official_confirmation"] is False
    assert items[1].published_at is None
    assert items[1].category == "token_unlock"


async def test_blockbeats_newsflash_parser_combines_local_date_and_time() -> None:
    source = _source(
        key="blockbeats_newsflash",
        name="BlockBeats Newsflash",
        source_type="chinese_media",
        adapter="media_html",
        url="https://m.theblockbeats.info/newsflash",
        canonical_url="https://m.theblockbeats.info/newsflash",
        category="newsflash",
        language="zh",
        config={
            "source_group": "media_zh",
            "parser": "blockbeats_newsflash_html",
            "parser_version": "blockbeats_newsflash_html_v1",
            "item_selector": "a[href^='/flash/']",
            "max_items": 10,
        },
    )

    items = await MediaHTMLAdapter().parse(
        source,
        _raw(source, "html_blockbeats_newsflash_live.html"),
    )

    assert len(items) == 2
    assert items[0].title == "Fed rate probability update"
    assert items[0].summary == "BlockBeats news, June 24, CME FedWatch data changed."
    assert items[0].url == "https://m.theblockbeats.info/flash/352778"
    assert items[0].published_at == datetime(2026, 6, 23, 17, 1, tzinfo=UTC)
    assert items[0].raw["provider_id"] == "352778"


async def test_media_json_parser_discards_full_content_field() -> None:
    source = _source(
        key="panews_public_json",
        name="PANews",
        source_type="chinese_media",
        adapter="json_api",
        url="https://example.com/api/news",
        canonical_url="https://example.com/api/news",
        category="market",
        language="zh",
        config={
            "source_group": "media_zh",
            "parser_version": "media_json_api_v1",
            "items_path": "data.items",
            "date_fields": ["publishedAt"],
            "tag_fields": ["tags"],
        },
    )
    items = await MediaJSONAPIAdapter().parse(source, _raw(source, "json_media_public_feed.json"))

    assert len(items) == 1
    assert items[0].category == "policy_regulatory"
    assert "完整文章正文" not in str(items[0].raw)
    assert "完整文章正文" not in (items[0].summary or "")
    assert items[0].raw["author"] == "PANews Reporter"


async def test_media_parsers_tolerate_empty_or_invalid_payloads() -> None:
    source = _source()
    empty = RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        body_hash=sha256(b"").hexdigest(),
        body="",
    )
    assert await MediaRSSAdapter().parse(source, empty) == []

    invalid_json_source = _source(adapter="json_api")
    invalid = empty.model_copy(update={"body": "{not-json"})
    assert await MediaJSONAPIAdapter().parse(invalid_json_source, invalid) == []


async def test_telegram_channel_parser_keeps_official_time_and_link() -> None:
    source = _source(
        key="hyperliquid_telegram_announcements",
        name="Hyperliquid Announcements",
        source_type="project_official",
        adapter="media_html",
        url="https://t.me/s/hyperliquid_announcements",
        canonical_url="https://t.me/hyperliquid_announcements",
        category="project_update",
        language="en",
        config={
            "source_group": "project_official",
            "official": True,
            "parser": "telegram_channel_html",
            "parser_version": "hyperliquid_telegram_channel_html_v1",
            "max_items": 20,
        },
    )

    items = await MediaHTMLAdapter().parse(source, _raw(source, "html_hyperliquid_telegram.html"))

    assert len(items) == 1
    assert items[0].url == "https://t.me/hyperliquid_announcements/123"
    assert items[0].published_at == datetime(2026, 5, 7, 10, 12, 13, tzinfo=UTC)
    assert items[0].raw["official_confirmation"] is True
    assert items[0].raw["provider_id"] == "hyperliquid_announcements/123"


async def test_backpack_blog_parser_uses_blog_date_and_skips_undated_cards() -> None:
    source = _source(
        key="backpack_blog",
        name="Backpack Blog",
        source_type="project_official",
        adapter="media_html",
        url="https://learn.backpack.exchange/blog",
        canonical_url="https://learn.backpack.exchange/blog",
        category="project_update",
        language="en",
        config={
            "source_group": "project_official",
            "official": True,
            "parser": "backpack_blog_html",
            "parser_version": "backpack_blog_html_v1",
            "max_items": 20,
        },
    )

    items = await MediaHTMLAdapter().parse(source, _raw(source, "html_backpack_blog.html"))

    assert len(items) == 1
    assert items[0].title == "Tokenized Micron (MU) is now live on Backpack"
    assert items[0].url == "https://learn.backpack.exchange/blog/tokenized-micron-mu"
    assert items[0].published_at == datetime(2026, 6, 22, tzinfo=UTC)
    assert items[0].raw["tags"] == ["Listings"]


async def test_aster_product_releases_parser_splits_weekly_release_blocks() -> None:
    source = _source(
        key="aster_product_releases",
        name="Aster Product Releases",
        source_type="project_official",
        adapter="media_html",
        url="https://docs.asterdex.com/trading/product-releases",
        canonical_url="https://docs.asterdex.com/trading/product-releases",
        category="project_update",
        language="en",
        config={
            "source_group": "project_official",
            "official": True,
            "parser": "aster_product_releases_html",
            "parser_version": "aster_product_releases_html_v1",
            "max_items": 20,
        },
    )

    items = await MediaHTMLAdapter().parse(
        source,
        _raw(source, "html_aster_product_releases.html"),
    )

    assert len(items) == 2
    assert items[0].title == "Aster product releases - Week starting 22/09/2025"
    assert items[0].url.endswith("?week_starting=2025-09-22")
    assert items[0].published_at == datetime(2025, 9, 22, tzinfo=UTC)
    assert "Portfolio page upgrade" in (items[0].summary or "")
    assert items[1].published_at == datetime(2025, 9, 15, tzinfo=UTC)
    assert items[0].raw["official_confirmation"] is True


async def test_betterstack_status_parser_emits_only_non_green_status() -> None:
    source = _source(
        key="backpack_status",
        name="Backpack Status",
        source_type="project_official",
        adapter="media_html",
        url="https://status.backpack.exchange/",
        canonical_url="https://status.backpack.exchange/",
        category="system_maintenance",
        language="en",
        config={
            "source_group": "project_official",
            "official": True,
            "parser": "betterstack_status_html",
            "parser_version": "backpack_status_html_v1",
            "max_items": 5,
        },
    )
    ok_items = await MediaHTMLAdapter().parse(source, _raw(source, "html_backpack_status_ok.html"))

    degraded_raw = _raw(source, "html_backpack_status_degraded.html").model_copy(
        update={"fetched_at": datetime(2026, 6, 24, 1, 0, tzinfo=UTC)}
    )
    degraded_items = await MediaHTMLAdapter().parse(source, degraded_raw)

    assert ok_items == []
    assert len(degraded_items) == 1
    assert degraded_items[0].published_at == datetime(2026, 6, 24, 1, 0, tzinfo=UTC)
    assert degraded_items[0].raw["official_confirmation"] is True