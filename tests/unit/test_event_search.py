from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from argon2 import PasswordHasher

from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.db.models import Event, EventAIInsight, EventSource, Source
from app.db.repositories.event_search_repo import EventSearchRepository
from app.db.session import get_session
from app.main import app
from app.schemas.event_search import EventSearchParams


def test_event_search_keyword_modes_filters_facets_and_pagination(db_session) -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    binance = _source("binance_listing", "Binance New Listings", "exchange_official", True)
    coindesk = _source("coindesk", "CoinDesk", "media_en", False)
    db_session.add_all([binance, coindesk])
    db_session.flush()

    events = [
        _event(
            "search:1",
            "币安将上线 ABC",
            "Binance will list ABC and open BTC pairs.",
            "listing",
            "confirmed",
            "high",
            ["ABC", "BTC"],
            ["BNB Chain"],
            now,
            trust_score=95,
        ),
        _event(
            "search:2",
            "Protocol exploit update",
            "A bridge exploit affects ETH liquidity.",
            "exploit",
            "needs_review",
            "critical",
            ["ETH"],
            ["Ethereum"],
            now - timedelta(minutes=1),
            trust_score=72,
        ),
        _event(
            "search:3",
            "Ordinary market note",
            "Market makers adjust quotes.",
            "market",
            "confirmed",
            "normal",
            ["SOL"],
            ["Solana"],
            now - timedelta(minutes=2),
            trust_score=55,
        ),
    ]
    db_session.add_all(events)
    db_session.flush()
    db_session.add_all(
        [
            EventSource(
                event_id=events[0].id,
                source_id=binance.id,
                url="https://binance/a",
                source_score=95,
            ),
            EventSource(
                event_id=events[1].id,
                source_id=coindesk.id,
                url="https://coindesk/b",
                source_score=75,
            ),
        ]
    )
    db_session.commit()

    repo = EventSearchRepository(db_session)
    zh = repo.search(EventSearchParams(q="上线", q_mode="phrase"))
    assert [item.event_key for item in zh.items] == ["search:1"]

    english = repo.search(EventSearchParams(q="BRIDGE EXPLOIT", q_mode="all"))
    assert [item.event_key for item in english.items] == ["search:2"]

    any_mode = repo.search(EventSearchParams(q="exploit listing", q_mode="any"))
    assert {item.event_key for item in any_mode.items} == {"search:1", "search:2"}

    filters = repo.search(
        EventSearchParams(
            symbols=["btc"],
            source_groups=["exchange_official"],
            official_only=True,
            minimum_trust_score=90,
        )
    )
    assert [item.event_key for item in filters.items] == ["search:1"]

    injected = repo.search(EventSearchParams(q="%' OR 1=1 --", q_mode="phrase"))
    assert injected.total == 0

    page_one = repo.search(EventSearchParams(page=1, page_size=2, sort="published_at"))
    page_two = repo.search(EventSearchParams(page=2, page_size=2, sort="published_at"))
    assert page_one.total == 3
    assert page_one.pages == 2
    assert {item.id for item in page_one.items}.isdisjoint({item.id for item in page_two.items})

    facets = repo.facets(EventSearchParams())
    assert _bucket_count(facets.categories, "listing") == 1
    assert _bucket_count(facets.severities, "critical") == 1
    assert _bucket_count(facets.source_groups, "exchange_official") == 1
    assert _bucket_count(facets.symbols, "BTC") == 1


def test_event_search_defaults_to_latest_first_seen_at(db_session) -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    latest_crawled = _event(
        "default:latest-crawled",
        "Older publish but newest crawl",
        "Fetched after the newer article was published.",
        "market",
        "confirmed",
        "normal",
        [],
        [],
        now - timedelta(days=1),
        trust_score=70,
    )
    latest_crawled.first_seen_at = now
    latest_published = _event(
        "default:latest-published",
        "Newer publish but older crawl",
        "Published later, but fetched earlier.",
        "market",
        "confirmed",
        "normal",
        [],
        [],
        now,
        trust_score=70,
    )
    latest_published.first_seen_at = now - timedelta(hours=1)
    db_session.add_all([latest_crawled, latest_published])
    db_session.commit()

    result = EventSearchRepository(db_session).search(EventSearchParams())

    assert result.sort == "first_seen_at"
    assert [item.event_key for item in result.items[:2]] == [
        "default:latest-crawled",
        "default:latest-published",
    ]


def test_event_search_filters_trust_focus_and_ai_importance_ranges(db_session) -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    important = _event(
        "quick:important",
        "Important listing",
        "High confidence listing",
        "listing",
        "confirmed",
        "high",
        ["BTC"],
        ["Bitcoin"],
        now,
        trust_score=92,
    )
    medium = _event(
        "quick:medium",
        "Medium confidence note",
        "Moderate confidence market note",
        "market",
        "confirmed",
        "normal",
        ["ETH"],
        ["Ethereum"],
        now - timedelta(minutes=1),
        trust_score=65,
    )
    low = _event(
        "quick:low",
        "Low confidence note",
        "Low confidence market note",
        "market",
        "confirmed",
        "low",
        ["SOL"],
        ["Solana"],
        now - timedelta(minutes=2),
        trust_score=45,
    )
    important.metadata_ = {"priority_score": 75, "priority_tier": "A"}
    medium.metadata_ = {"priority_score": 45, "priority_tier": "noise"}
    low.metadata_ = {"priority_score": 30, "priority_tier": "noise"}
    db_session.add_all([important, medium, low])
    db_session.flush()
    db_session.add_all(
        [
            _insight(
                important.id,
                "important",
                status="success",
                importance_score=72,
                generated_at=now,
            ),
            _insight(medium.id, "medium", status="success", importance_score=45, generated_at=now),
            _insight(low.id, "low", status="failed", importance_score=99, generated_at=now),
        ]
    )
    db_session.commit()

    repo = EventSearchRepository(db_session)

    medium_trust = repo.search(EventSearchParams(minimum_trust_score=60, maximum_trust_score=79))
    assert [item.event_key for item in medium_trust.items] == ["quick:medium"]

    not_important = repo.search(EventSearchParams(maximum_priority_score=59, sort="priority_score"))
    assert {item.event_key for item in not_important.items} == {"quick:medium", "quick:low"}

    ai_key = repo.search(EventSearchParams(has_ai_summary=True, minimum_ai_importance_score=60))
    assert [item.event_key for item in ai_key.items] == ["quick:important"]

    without_successful_ai = repo.search(EventSearchParams(has_ai_summary=False))
    assert [item.event_key for item in without_successful_ai.items] == ["quick:low"]


@pytest.mark.asyncio
async def test_admin_event_search_api_defaults_to_latest_first_seen_at(
    monkeypatch, db_session
) -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    latest_crawled = _event(
        "api:latest-crawled",
        "Older publish but newest crawl",
        "Fetched after the newer article was published.",
        "market",
        "confirmed",
        "normal",
        [],
        [],
        now - timedelta(days=1),
        trust_score=70,
    )
    latest_crawled.first_seen_at = now
    latest_published = _event(
        "api:latest-published",
        "Newer publish but older crawl",
        "Published later, but fetched earlier.",
        "market",
        "confirmed",
        "normal",
        [],
        [],
        now,
        trust_score=70,
    )
    latest_published.first_seen_at = now - timedelta(hours=1)
    db_session.add_all([latest_crawled, latest_published])
    db_session.commit()

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(monkeypatch, db_session, client)
            response = await client.get("/api/admin/events", headers={"x-csrf-token": csrf})
            assert response.status_code == 200
            body = response.json()
            assert body["sort"] == "first_seen_at"
            assert [item["event_key"] for item in body["items"][:2]] == [
                "api:latest-crawled",
                "api:latest-published",
            ]
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_admin_event_search_api_rejects_sort_injection(monkeypatch, db_session) -> None:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(monkeypatch, db_session, client)
            response = await client.get(
                "/api/admin/events",
                params={"sort": "published_at;drop table events"},
                headers={"x-csrf-token": csrf},
            )
            assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_search_crud_api(monkeypatch, db_session) -> None:
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(monkeypatch, db_session, client)
            created = await client.post(
                "/api/admin/saved-searches",
                headers={"x-csrf-token": csrf},
                json={
                    "name": "BTC 高优先级",
                    "description": "高可信 BTC 事件",
                    "filters": {
                        "q": "BTC",
                        "q_mode": "all",
                        "symbols": ["BTC"],
                        "severities": ["high", "critical"],
                        "minimum_trust_score": 80,
                    },
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["filters"]["symbols"] == ["BTC"]
            assert body["owner_subject"] == "admin"

            listed = await client.get("/api/admin/saved-searches")
            assert listed.status_code == 200
            assert [item["name"] for item in listed.json()] == ["BTC 高优先级"]

            patched = await client.patch(
                f"/api/admin/saved-searches/{body['id']}",
                headers={"x-csrf-token": csrf},
                json={"name": "BTC 严重事件", "filters": {"q": "BTC", "q_mode": "phrase"}},
            )
            assert patched.status_code == 200
            assert patched.json()["name"] == "BTC 严重事件"
            assert patched.json()["filters"]["q_mode"] == "phrase"

            deleted = await client.delete(
                f"/api/admin/saved-searches/{body['id']}",
                headers={"x-csrf-token": csrf},
            )
            assert deleted.status_code == 204
            assert (await client.get("/api/admin/saved-searches")).json() == []
    finally:
        app.dependency_overrides.clear()


def _source(key: str, name: str, source_group: str, official: bool) -> Source:
    return Source(
        key=key,
        name=name,
        source_type=source_group,
        adapter="rss",
        url=f"https://example.com/{key}.xml",
        canonical_url=f"https://example.com/{key}.xml",
        category="listing",
        language="en",
        trust_score=90,
        poll_seconds=120,
        timeout_seconds=10,
        max_response_bytes=1024 * 1024,
        enabled=True,
        config={"source_group": source_group, "official": official},
    )


def _event(
    key: str,
    title: str,
    summary: str,
    category: str,
    status: str,
    severity: str,
    symbols: list[str],
    chains: list[str],
    published_at: datetime,
    *,
    trust_score: int,
) -> Event:
    return Event(
        event_key=key,
        title=title,
        summary=summary,
        category=category,
        status=status,
        severity=severity,
        language="zh-CN" if any("\u4e00" <= char <= "\u9fff" for char in title) else "en",
        primary_url=f"https://example.com/{key}",
        published_at=published_at,
        first_seen_at=published_at,
        last_seen_at=published_at,
        trust_score=trust_score,
        confirmation_count=1,
        symbols=symbols,
        chains=chains,
        entities=[],
        metadata_={},
    )


def _insight(
    event_id: int,
    suffix: str,
    *,
    status: str,
    importance_score: int,
    generated_at: datetime,
) -> EventAIInsight:
    return EventAIInsight(
        event_id=event_id,
        provider="deepseek",
        model="mock",
        prompt_version="v1",
        input_hash=f"hash-{suffix}",
        summary_zh=f"summary {suffix}" if status == "success" else None,
        headline_zh=f"headline {suffix}" if status == "success" else None,
        key_facts=[],
        entities=[],
        symbols=[],
        chains=[],
        event_type="market",
        importance_score=importance_score,
        risk_level="low",
        sentiment="neutral",
        market_impact=None,
        facts=[],
        inferences=[],
        confidence=0.8,
        source_event_ids=[],
        source_urls=[],
        input_quality="summary",
        prompt_tokens=1,
        completion_tokens=1,
        generated_at=generated_at,
        status=status,
    )
def _bucket_count(buckets, key: str) -> int:
    return next((bucket.count for bucket in buckets if bucket.key == key), 0)


async def _login(monkeypatch, db_session, client: httpx.AsyncClient) -> str:
    _login_failures.clear()
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    response = await client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])
