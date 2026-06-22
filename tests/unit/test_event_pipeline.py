from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import (
    AIRun,
    Delivery,
    Event,
    EventAIInsight,
    EventSource,
    FetchRun,
    NotificationDestination,
    RawDocument,
    Source,
)
from app.pipeline.event_pipeline import build_event_pipeline


def test_event_pipeline_combines_fetch_ai_and_feishu_status(db_session) -> None:
    now = datetime.now(UTC)
    source = Source(
        key="fixture",
        name="Fixture News",
        display_name_zh="测试来源",
        source_group="media_en",
        source_type="rss",
        adapter="rss",
        url="https://example.com/rss.xml",
        canonical_url="https://example.com/rss.xml",
        category="market",
        language="en",
        trust_score=80,
    )
    fetch_run = FetchRun(
        source=source,
        status="success",
        queued_at=now - timedelta(seconds=3),
        worker_started_at=now - timedelta(seconds=2),
        started_at=now - timedelta(seconds=3),
        finished_at=now,
        http_status=200,
        item_count=1,
        trace_id="trace-1",
    )
    raw_document = RawDocument(
        source=source,
        fetch_run=fetch_run,
        url="https://example.com/news/btc",
        canonical_url="https://example.com/news/btc",
        content_type="application/json",
        status_code=200,
        body_hash="hash-1",
        body='{"summary":"BTC market summary"}',
        metadata_={"summary": "BTC market summary"},
        fetched_at=now,
    )
    event = Event(
        event_key="fixture:btc",
        title="BTC 市场波动",
        summary="BTC 出现明显波动。",
        category="market",
        status="confirmed",
        severity="high",
        language="zh",
        primary_url="https://example.com/news/btc",
        published_at=now,
        first_seen_at=now,
        last_seen_at=now,
        trust_score=82,
        confirmation_count=1,
        symbols=["BTC"],
        chains=["Bitcoin"],
    )
    event_source = EventSource(
        event=event,
        source=source,
        raw_document=raw_document,
        url="https://example.com/news/btc",
        title=event.title,
        published_at=now,
        source_score=80,
    )
    insight = EventAIInsight(
        event=event,
        provider="deepseek",
        model="mock-model",
        prompt_version="v1",
        input_hash="input-hash",
        summary_zh="BTC 出现明显波动。",
        headline_zh="BTC 市场波动",
        event_type="market",
        importance_score=70,
        risk_level="medium",
        sentiment="mixed",
        market_impact="短线波动加大",
        confidence=0.8,
        source_event_ids=[str(event.id)],
        source_urls=["https://example.com/news/btc"],
        input_quality="summary",
        status="succeeded",
        generated_at=now,
    )
    job = AIRun(
        job_type="summarize_event",
        provider="deepseek",
        model="mock-model",
        event_count=1,
        status="succeeded",
        event_ids=[],
        queued_at=now - timedelta(seconds=4),
        started_at=now - timedelta(seconds=3),
        finished_at=now - timedelta(seconds=1),
        queue_wait_ms=1000,
        provider_latency_ms=2000,
        total_latency_ms=3000,
    )
    destination = NotificationDestination(
        key="feishu-test",
        name="飞书测试群",
        provider="feishu_webhook",
        enabled=True,
        status="active",
        activated_at=now - timedelta(minutes=1),
    )
    delivery = Delivery(
        event=event,
        destination=destination,
        channel="feishu",
        target="feishu-test",
        status="delivered",
        idempotency_key="delivery-key",
        delivery_variant="immediate",
        provider_message_id="mock-message",
        attempts=1,
        delivered_at=now,
    )

    db_session.add_all(
        [source, fetch_run, raw_document, event, event_source, insight, job, destination, delivery]
    )
    db_session.flush()
    job.event_ids = [event.id]
    db_session.flush()

    pipeline = build_event_pipeline(db_session, event)

    assert pipeline.source.status == "fetched"
    assert pipeline.source.fetch_run_id == fetch_run.id
    assert pipeline.source.queue_wait_ms == 1000
    assert pipeline.event.status == "confirmed"
    assert pipeline.ai.status == "succeeded"
    assert pipeline.ai.input_quality == "summary"
    assert pipeline.ai.queue_wait_ms == 1000
    assert pipeline.deliveries[0].status == "delivered"
    assert pipeline.deliveries[0].destination_name == "飞书测试群"
    assert pipeline.metrics["delivery_count"] == 1


def test_event_pipeline_marks_dry_run_delivery(db_session) -> None:
    now = datetime.now(UTC)
    event = Event(
        event_key="fixture:dry-run",
        title="测试 dry-run",
        category="system",
        status="confirmed",
        severity="normal",
        first_seen_at=now,
        last_seen_at=now,
        trust_score=100,
        confirmation_count=1,
    )
    delivery = Delivery(
        event=event,
        channel="feishu",
        target="feishu-test",
        status="delivered",
        idempotency_key="dry-run-key",
        delivery_variant="immediate",
        provider_message_id="dry-run",
        attempts=1,
    )
    db_session.add_all([event, delivery])
    db_session.flush()

    pipeline = build_event_pipeline(db_session, event)

    assert pipeline.source.status == "queued"
    assert pipeline.ai.status == "not_requested"
    assert pipeline.deliveries[0].status == "dry_run"


def test_event_pipeline_treats_legacy_success_insight_as_succeeded(db_session) -> None:
    now = datetime.now(UTC)
    event = Event(
        event_key="fixture:legacy-ai",
        title="旧状态 AI",
        category="market",
        status="confirmed",
        severity="normal",
        first_seen_at=now,
        last_seen_at=now,
        trust_score=80,
        confirmation_count=1,
    )
    insight = EventAIInsight(
        event=event,
        provider="deepseek",
        model="mock",
        prompt_version="v1",
        input_hash="legacy",
        summary_zh="旧 success 状态摘要",
        headline_zh="旧状态 AI",
        event_type="market",
        importance_score=50,
        risk_level="low",
        sentiment="neutral",
        market_impact="不确定",
        confidence=0.5,
        input_quality="summary",
        status="success",
        generated_at=now,
    )
    db_session.add_all([event, insight])
    db_session.flush()

    pipeline = build_event_pipeline(db_session, event)

    assert pipeline.ai.status == "succeeded"
    assert pipeline.ai.input_quality == "summary"
