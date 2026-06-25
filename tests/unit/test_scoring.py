from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import Event, EventSource, Source
from app.pipeline.scoring import ScoringService

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def test_official_source_confirms_event() -> None:
    result = _score(
        title="Binance Will List ABC",
        category="listing",
        source_type="exchange_official",
        source_score=95,
        symbols=["ABC"],
    )

    assert result.status == "confirmed"
    assert result.severity == "high"
    assert result.trust_score == 95


def test_exchange_activity_scores_as_alpha_signal() -> None:
    result = _score(
        title="Binance Launchpool opens ABC farming and will list ABC spot trading",
        category="listing",
        source_type="exchange_official",
        source_score=95,
        symbols=["ABC"],
    )

    assert result.priority_score >= 85
    assert result.priority_tier == "S"
    assert "alpha:exchange activity" in result.reasons


def test_project_interaction_scores_high_for_alpha_player() -> None:
    result = _score(
        title="Aster launches points campaign testnet quests for airdrop eligibility",
        category="project_update",
        source_type="project_official",
        source_score=90,
        symbols=["ASTER"],
    )

    assert result.priority_score >= 70
    assert result.priority_tier in {"A", "S"}
    assert "alpha:project interaction" in result.reasons


def test_onchain_whale_flow_scores_high_for_alpha_player() -> None:
    result = _score(
        title="Smart money whale accumulates HYPE and moves funds on Hyperliquid",
        category="onchain",
        source_type="onchain_data",
        source_score=85,
        symbols=["HYPE"],
    )

    assert result.priority_score >= 70
    assert result.priority_tier in {"A", "S"}
    assert "alpha:on-chain flow" in result.reasons


def test_hot_project_update_stays_visible_without_direct_activity() -> None:
    result = _score(
        title="Aster launches privacy-focused Layer 1 network",
        category="project_update",
        source_type="news_aggregator",
        source_score=55,
    )

    assert result.priority_score >= 55
    assert result.priority_tier == "B"
    assert "alpha:hot project" in result.reasons


def test_prediction_market_price_item_is_deprioritized() -> None:
    result = _score(
        title="HYPE price on Jun 24 Crypto Prediction Market",
        category="market",
        source_type="news_aggregator",
        source_score=55,
        symbols=["HYPE"],
    )

    assert result.priority_score < 55
    assert result.priority_tier == "noise"
    assert "generic analysis" in result.noise_reasons


def test_stock_perp_listing_is_capped_below_core_crypto_alpha() -> None:
    result = _score(
        title="Bitget Announcement: Listing of SONYUSDT and MVLLUSDT Hot Stock Perps",
        category="listing",
        source_type="exchange_official",
        source_score=95,
        symbols=["SONYUSDT", "MVLLUSDT"],
    )

    assert result.priority_score < 70
    assert result.priority_tier in {"B", "noise"}
    assert "traditional market/equity" in result.noise_reasons


def test_generic_trend_article_cannot_reach_s_tier_even_if_misclassified() -> None:
    result = _score(
        title="DeFi TVL drops 39% in 2026 amid market downturn and record hack activity",
        category="exploit",
        source_type="news_aggregator",
        source_score=55,
    )

    assert result.priority_score <= 62
    assert result.priority_tier in {"B", "noise"}
    assert "generic analysis" in result.noise_reasons


def test_low_alpha_regulatory_personnel_update_is_deprioritized() -> None:
    result = _score(
        title="SEC appoints new director of international affairs office",
        category="policy_regulatory",
        source_type="regulator_official",
        source_score=100,
    )

    assert result.priority_score < 55
    assert result.priority_tier == "noise"
    assert "admin update" in result.noise_reasons
    assert "low alpha regulatory update" in result.noise_reasons


def _score(
    *,
    title: str,
    category: str,
    source_type: str,
    source_score: int,
    symbols: list[str] | None = None,
):
    source = Source(
        id=1,
        key="test-source",
        name="Test Source",
        source_type=source_type,
        adapter="rss",
        url="https://example.com",
        canonical_url="https://example.com",
        category=category,
        trust_score=source_score,
        poll_seconds=30,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={},
    )
    event = Event(
        id=1,
        event_key="event:test",
        title=title,
        summary="summary",
        category=category,
        status="needs_review",
        severity="normal",
        language="en",
        primary_url="https://example.com/1",
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        trust_score=50,
        confirmation_count=1,
        symbols=symbols or [],
        chains=[],
        entities=[],
        metadata_={},
    )
    link = EventSource(
        event_id=1,
        source_id=1,
        url="https://example.com/1",
        source_score=source_score,
        source=source,
    )
    return ScoringService().score(event, [link])
