from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import (
    Delivery,
    Event,
    EventAIInsight,
    NotificationDestination,
    NotificationRule,
)
from app.integrations.feishu.models import FeishuSendResult
from app.pipeline.destination_router import DestinationRouter
from app.publishers.feishu import publish_feishu_once


class FakeFeishuClient:
    def __init__(self) -> None:
        self.cards: list[dict] = []

    async def send_interactive_card(self, chat_id: str, card: dict) -> FeishuSendResult:
        self.cards.append({"chat_id": chat_id, "card": card})
        return FeishuSendResult(ok=True, message_id="om_mock", status_code=200)


class FailingFeishuClient:
    async def send_interactive_card(self, chat_id: str, card: dict) -> FeishuSendResult:
        raise RuntimeError("network token secret leaked")


def test_feishu_pipeline_dry_run_when_send_disabled(monkeypatch, db_session) -> None:
    now = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
    monkeypatch.setattr("app.core.config.settings.feishu_enabled", True)
    monkeypatch.setattr("app.core.config.settings.feishu_send_enabled", False)
    destination = _destination(now, key="feishu-dry-run")
    event = _event("event:dry-run", now)
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()

    decision = DestinationRouter(db_session).should_route(event, destination, now=now)
    fake_client = FakeFeishuClient()
    delivery = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )

    assert decision.should_send is True
    assert delivery.status == "delivered"
    assert delivery.provider_message_id == "dry-run"
    assert fake_client.cards == []
    assert db_session.scalar(select(func.count(Delivery.id))) == 1


def test_feishu_pipeline_uses_mock_client_and_deduplicates_variant(
    monkeypatch, db_session
) -> None:
    now = datetime(2026, 6, 21, 10, 5, tzinfo=UTC)
    monkeypatch.setattr("app.core.config.settings.feishu_enabled", True)
    monkeypatch.setattr("app.core.config.settings.feishu_send_enabled", True)
    destination = _destination(now, key="feishu-send")
    event = _event(
        "event:send",
        now,
        summary="基础摘要",
        ai_summary="AI 风险摘要",
    )
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()

    router = DestinationRouter(db_session)
    decision = router.should_route(event, destination, now=now)
    fake_client = FakeFeishuClient()
    first = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )
    second_decision = router.should_route(event, destination, now=now)
    second = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )

    assert decision.should_send is True
    assert first.id == second.id
    assert first.status == "delivered"
    assert first.provider_message_id == "om_mock"
    assert len(fake_client.cards) == 1
    assert "AI 风险摘要" in json.dumps(fake_client.cards[0]["card"], ensure_ascii=False)
    assert "基础摘要" not in json.dumps(fake_client.cards[0]["card"], ensure_ascii=False)
    assert second_decision.should_send is False
    assert second_decision.reason == "delivery_idempotency"
    assert db_session.scalar(select(func.count(Delivery.id))) == 1


def test_feishu_send_exception_marks_failed_and_allows_retry(monkeypatch, db_session) -> None:
    now = datetime(2026, 6, 21, 10, 6, tzinfo=UTC)
    monkeypatch.setattr("app.core.config.settings.feishu_enabled", True)
    monkeypatch.setattr("app.core.config.settings.feishu_send_enabled", True)
    destination = _destination(now, key="feishu-retry")
    event = _event("event:retry", now, ai_summary="AI 摘要")
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()

    decision = DestinationRouter(db_session).should_route(event, destination, now=now)
    failed = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=FailingFeishuClient(),
            delivery_variant=decision.delivery_mode,
        )
    )

    assert failed.status == "failed"
    assert failed.last_error == "Feishu delivery failed"
    assert DestinationRouter(db_session).should_route(event, destination, now=now).should_send

    fake_client = FakeFeishuClient()
    retried = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )

    assert retried.id == failed.id
    assert retried.status == "delivered"
    assert retried.provider_message_id == "om_mock"
    assert len(fake_client.cards) == 1
    assert db_session.scalar(select(func.count(Delivery.id))) == 1


def test_delivered_feishu_retry_is_idempotent(monkeypatch, db_session) -> None:
    now = datetime(2026, 6, 21, 10, 7, tzinfo=UTC)
    monkeypatch.setattr("app.core.config.settings.feishu_enabled", True)
    monkeypatch.setattr("app.core.config.settings.feishu_send_enabled", True)
    destination = _destination(now, key="feishu-delivered")
    event = _event("event:delivered", now, ai_summary="AI 摘要")
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()
    decision = DestinationRouter(db_session).should_route(event, destination, now=now)
    fake_client = FakeFeishuClient()
    delivered = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )

    second = asyncio.run(
        publish_feishu_once(
            db_session,
            event,
            destination,
            client=fake_client,
            delivery_variant=decision.delivery_mode,
        )
    )

    assert delivered.id == second.id
    assert second.status == "delivered"
    assert len(fake_client.cards) == 1


def test_destination_router_blocks_historical_feishu_event(db_session) -> None:
    activated_at = datetime(2026, 6, 21, 10, 10, tzinfo=UTC)
    destination = _destination(activated_at, key="feishu-history")
    event = _event("event:history", destination.activated_at - timedelta(seconds=1))
    db_session.add_all([destination, event, _rule(destination)])
    db_session.flush()

    decision = DestinationRouter(db_session).should_route(event, destination, now=activated_at)

    assert decision.should_send is False
    assert decision.reason == "historical_event_protected"
    assert db_session.scalar(select(func.count(Delivery.id))) == 0


def _destination(now: datetime, *, key: str) -> NotificationDestination:
    return NotificationDestination(
        key=key,
        name="Feishu test",
        provider="feishu_app",
        enabled=True,
        status="active",
        chat_id=f"oc_{key}",
        chat_name="Feishu test",
        config={},
        activated_at=now - timedelta(minutes=1),
    )


def _rule(destination: NotificationDestination) -> NotificationRule:
    return NotificationRule(
        destination=destination,
        name="default",
        enabled=True,
        minimum_severity="normal",
        categories=[],
        sources=[],
        symbols=[],
        chains=[],
        delivery_mode="immediate",
        timezone="UTC",
        maximum_messages_per_hour=30,
    )


def _event(
    key: str,
    first_seen_at: datetime,
    *,
    summary: str = "事件摘要",
    ai_summary: str | None = None,
) -> Event:
    insights = []
    if ai_summary:
        insights.append(
            EventAIInsight(
                provider="deepseek",
                model="mock",
                prompt_version="v1",
                input_hash=key,
                summary_zh=ai_summary,
                status="success",
            )
        )
    return Event(
        event_key=key,
        title="安全事件",
        summary=summary,
        category="security",
        status="confirmed",
        severity="high",
        language="zh-CN",
        primary_url="https://example.com/news",
        published_at=first_seen_at,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        trust_score=90,
        confirmation_count=2,
        symbols=["ETH"],
        chains=["Ethereum"],
        entities=[],
        metadata_={},
        ai_insights=insights,
    )
