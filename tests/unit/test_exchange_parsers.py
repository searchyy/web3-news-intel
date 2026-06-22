from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.adapters.exchanges import ExchangeOfficialAdapter
from app.core.config import SourceConfig
from app.core.time import utc_now
from app.parsers.exchanges.common import SUPPORTED_EXCHANGE_CATEGORIES
from app.parsers.exchanges.html_parser import parse_html_announcements
from app.parsers.exchanges.json_parser import parse_json_announcements
from app.schemas.raw_document import RawDocumentPayload

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "source_catalog" / "exchanges.yaml"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "exchanges"

TOP10_KEYS = [
    "coinbase_exchange",
    "binance_announcements",
    "kraken_announcements",
    "bitget_announcements",
    "okx_announcements",
    "bybit_announcements",
    "bitstamp_announcements",
    "gate_announcements",
    "mexc_announcements",
    "hashkey_announcements",
]

FIXTURE_CASES = [
    ("coinbase_exchange", "coinbase_exchange_rss.xml", "listing"),
    ("binance_announcements", "binance_announcements_json.json", "listing"),
    ("kraken_announcements", "kraken_announcements_rss.xml", "listing"),
    ("bitget_announcements", "bitget_announcements_html.html", "listing"),
    ("okx_announcements", "okx_announcements_html.html", "listing"),
    ("bybit_announcements", "bybit_announcements_json.json", "derivatives_listing"),
    ("bitstamp_announcements", "bitstamp_announcements_rss.xml", "trading_rule"),
    ("gate_announcements", "gate_announcements_json.json", "delisting"),
    ("mexc_announcements", "mexc_announcements_json.json", "deposit_withdrawal"),
    ("hashkey_announcements", "hashkey_announcements_html.html", "security_incident"),
]


def test_exchange_catalog_covers_top10_and_candidates() -> None:
    catalog = _catalog()
    sources = catalog["sources"]
    assert [sources[key]["ranking_position"] for key in TOP10_KEYS] == list(range(1, 11))
    for key in TOP10_KEYS:
        source = sources[key]
        assert source["source_group"] == "exchange_official"
        assert source["official"] is True
        assert source["ranking_provider"] == "CoinGecko Trust Score"
        assert source["ranking_snapshot_at"] == "2026-06-21"
        assert source["parser_version"]
        assert source["max_items_per_fetch"] <= 20
        assert set(source["supported_categories"]).issubset(SUPPORTED_EXCHANGE_CATEGORIES)
        assert source["live_canary_status"] in {
            "NOT_RUN",
            "PASS",
            "DEGRADED",
            "ACCESS_DENIED",
            "EMPTY",
            "PARSER_BROKEN",
            "NETWORK_FAILED",
            "DISABLED",
            "UNSUPPORTED",
        }
        if source["live_canary_status"] == "DISABLED":
            assert source["enabled"] is False
            assert source["last_canary_error"]
    for candidate in (
        "kucoin_announcements",
        "upbit_announcements",
        "htx_announcements",
        "crypto_com_exchange_announcements",
    ):
        assert sources[candidate]["enabled"] is False
        assert sources[candidate]["ranking_position"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_key", "fixture_name", "expected_category"), FIXTURE_CASES)
async def test_exchange_top10_fixtures_parse(
    source_key: str,
    fixture_name: str,
    expected_category: str,
) -> None:
    source = _source_from_catalog(source_key)
    raw = _raw(source, fixture_name)
    items = await ExchangeOfficialAdapter().parse(source, raw)
    assert items
    assert items[0].source_key == source_key
    assert items[0].source_type == "exchange_official"
    assert items[0].raw["source_group"] == "exchange_official"
    assert items[0].raw["official"] is True
    assert items[0].raw["parser_version"] == source.config["parser_version"]
    assert items[0].raw["content_hash"]
    assert items[0].category == expected_category
    assert items[0].canonical_url


def test_exchange_json_parser_handles_empty_list() -> None:
    source = _json_source(items_path="data.catalogs.0.articles")
    assert parse_json_announcements(source, _raw(source, "json_empty.json")) == []


def test_exchange_json_parser_handles_bad_date_without_failing() -> None:
    source = _json_source()
    items = parse_json_announcements(source, _raw(source, "json_bad_dates.json"))
    assert len(items) == 1
    assert items[0].published_at is None


def test_exchange_json_parser_deduplicates_by_canonical_url() -> None:
    source = _json_source()
    items = parse_json_announcements(source, _raw(source, "json_duplicate.json"))
    assert len(items) == 1
    assert items[0].raw["content_hash"]


def test_exchange_json_parser_skips_missing_required_fields() -> None:
    source = _json_source()
    items = parse_json_announcements(source, _raw(source, "json_missing_fields.json"))
    assert [item.title for item in items] == ["Exchange Will List VALID"]


def test_exchange_canary_limits_to_ten_items() -> None:
    source = _json_source()
    raw = _raw(source, "json_many.json", canary=True)
    items = parse_json_announcements(source, raw)
    assert len(items) == 10


def test_exchange_html_parser_handles_missing_selectors() -> None:
    source = _source_from_catalog("hashkey_announcements")
    source = source.model_copy(
        update={
            "config": {
                **source.config,
                "item_selector": ".does-not-exist",
                "title_selector": ".title",
            }
        }
    )
    items = parse_html_announcements(source, _raw(source, "hashkey_announcements_html.html"))
    assert items == []


@pytest.mark.asyncio
async def test_exchange_adapter_sends_conditional_request_headers() -> None:
    source = _source_from_catalog("binance_announcements")
    client = FakeFetchClient(body=fixture_text("binance_announcements_json.json"))
    raw = await ExchangeOfficialAdapter().fetch(
        source,
        client,  # type: ignore[arg-type]
        etag='"abc"',
        last_modified="Fri, 19 Jun 2026 12:00:00 GMT",
        canary=True,
    )
    assert client.headers == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Fri, 19 Jun 2026 12:00:00 GMT",
    }
    assert raw[0].metadata["etag"] == '"next"'
    assert raw[0].metadata["last_modified"] == "Sat, 20 Jun 2026 12:00:00 GMT"
    assert raw[0].metadata["canary"] is True
    assert raw[0].metadata["max_canary_items"] == 10


def _catalog() -> dict[str, Any]:
    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))


def _source_from_catalog(key: str) -> SourceConfig:
    entry = _catalog()["sources"][key]
    config = dict(entry.get("config") or {})
    config.update(
        {
            "parser": entry["parser"],
            "parser_version": entry["parser_version"],
            "source_group": entry["source_group"],
            "official": entry["official"],
            "max_items_per_fetch": entry["max_items_per_fetch"],
        }
    )
    return SourceConfig(
        key=key,
        name=entry["name"],
        source_type=entry["source_type"],
        adapter=entry["adapter"],
        url=entry["url"],
        canonical_url=entry["canonical_url"],
        category=entry["category"],
        language=entry["language"],
        trust_score=entry["trust_score"],
        poll_seconds=entry["poll_seconds"],
        timeout_seconds=entry["timeout_seconds"],
        max_response_bytes=entry["maximum_response_bytes"],
        enabled=entry["enabled"],
        config=config,
    )


def _json_source(**config_overrides: Any) -> SourceConfig:
    config = {
        "parser": "exchange_json",
        "parser_version": "test_exchange_json_v1",
        "source_group": "exchange_official",
        "official": True,
        "items_path": "items",
        "title_fields": ["title"],
        "summary_fields": ["description", "summary"],
        "date_fields": ["published_at"],
        "url_fields": ["url"],
        "id_fields": ["id"],
        "max_items_per_fetch": 20,
    }
    config.update(config_overrides)
    return SourceConfig(
        key="test_exchange",
        name="Test Exchange",
        source_type="exchange_official",
        adapter="json_api",
        url="https://example.com/announcements",
        canonical_url="https://example.com/announcements",
        category="product",
        language="en",
        trust_score=90,
        poll_seconds=120,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        config=config,
    )


def _raw(
    source: SourceConfig,
    fixture_name: str,
    *,
    canary: bool = False,
) -> RawDocumentPayload:
    body = fixture_text(fixture_name)
    return RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        canonical_url=source.canonical_url,
        content_type="application/json",
        status_code=200,
        body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        body=body,
        metadata={"canary": canary},
    )


def fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@dataclass
class FakeFetchResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str
    content_type: str
    body_hash: str
    fetched_at: Any


class FakeFetchClient:
    def __init__(self, body: str):
        self.body = body
        self.headers: dict[str, str] | None = None

    async def get_text(self, url: str, **kwargs: Any) -> FakeFetchResponse:
        self.headers = kwargs.get("headers")
        return FakeFetchResponse(
            url=url,
            status_code=200,
            headers={
                "etag": '"next"',
                "last-modified": "Sat, 20 Jun 2026 12:00:00 GMT",
            },
            text=self.body,
            content_type="application/json",
            body_hash=hashlib.sha256(self.body.encode("utf-8")).hexdigest(),
            fetched_at=utc_now(),
        )
