from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.time import utc_now
from app.db.models import Delivery, Event, NotificationDestination, ReportSchedule, SavedSearch
from app.integrations.feishu.reporting import FeishuReportService


def test_report_schedule_window_idempotency(db_session) -> None:
    now = datetime(2026, 6, 21, 8, 0, tzinfo=UTC)
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    event = _event("event:btc", "BTC 上币公告", now - timedelta(minutes=5), symbols=["BTC"])
    schedule = _schedule(destination, activated_at=now - timedelta(hours=1))
    db_session.add_all([event, schedule])
    db_session.flush()

    service = FeishuReportService(db_session)
    first = asyncio.run(
        service.send_report_for_window(
            schedule,
            window_start=now - timedelta(minutes=15),
            window_end=now,
        )
    )
    second = asyncio.run(
        service.send_report_for_window(
            schedule,
            window_start=now - timedelta(minutes=15),
            window_end=now,
        )
    )

    assert first.status == "sent"
    assert second.status == "duplicate"
    assert db_session.scalar(select(func.count(Delivery.id))) == 1


def test_report_summary_uses_deterministic_fallback_without_ai(db_session) -> None:
    now = utc_now()
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    schedule = _schedule(
        destination,
        include_ai_summary=True,
        activated_at=now - timedelta(hours=1),
    )
    event = _event("event:security", "安全事件", now - timedelta(minutes=10), severity="high")
    db_session.add_all([schedule, event])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)

    assert "确定性规则模板" in preview.summary_zh
    assert preview.critical_high_count == 1


def test_report_blocks_events_before_schedule_activation(db_session) -> None:
    now = utc_now()
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    schedule = _schedule(destination, activated_at=now - timedelta(minutes=5))
    old_event = _event("event:old", "启用前历史事件", now - timedelta(minutes=30))
    db_session.add_all([schedule, old_event])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)

    assert preview.event_count == 0


def test_saved_search_filters_feed_report(db_session) -> None:
    now = utc_now()
    destination = _destination(now)
    saved_search = SavedSearch(
        name="BTC 安全筛选",
        filters={"categories": ["security"], "symbols": ["BTC"]},
        owner_subject="admin",
    )
    db_session.add_all([destination, saved_search])
    db_session.flush()
    schedule = _schedule(
        destination,
        saved_search_id=saved_search.id,
        activated_at=now - timedelta(hours=1),
    )
    wanted = _event(
        "event:wanted",
        "BTC 安全事件",
        now - timedelta(minutes=10),
        category="security",
        symbols=["BTC"],
    )
    ignored = _event(
        "event:ignored",
        "ETH 协议事件",
        now - timedelta(minutes=10),
        category="protocol",
        symbols=["ETH"],
    )
    db_session.add_all([schedule, wanted, ignored])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)

    assert [event.event_key for event in preview.events] == ["event:wanted"]


def test_duplicate_schedules_share_one_delivery_for_same_window(db_session) -> None:
    now = datetime(2026, 6, 21, 9, 0, tzinfo=UTC)
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    event = _event("event:shared", "同窗口事件", now - timedelta(minutes=3))
    first_schedule = _schedule(destination, activated_at=now - timedelta(hours=1))
    second_schedule = _schedule(destination, name="重复汇报", activated_at=now - timedelta(hours=1))
    db_session.add_all([event, first_schedule, second_schedule])
    db_session.flush()
    service = FeishuReportService(db_session)

    asyncio.run(
        service.send_report_for_window(
            first_schedule,
            window_start=now - timedelta(minutes=15),
            window_end=now,
        )
    )
    asyncio.run(
        service.send_report_for_window(
            second_schedule,
            window_start=now - timedelta(minutes=15),
            window_end=now,
        )
    )

    assert db_session.scalar(select(func.count(Delivery.id))) == 1


def _destination(now: datetime) -> NotificationDestination:
    return NotificationDestination(
        key=f"feishu-{now.timestamp()}",
        name="飞书测试群",
        provider="feishu_app",
        enabled=True,
        status="active",
        chat_id="oc_test",
        chat_name="飞书测试群",
        config={},
        activated_at=now,
    )


def _schedule(
    destination: NotificationDestination,
    *,
    name: str = "每小时汇报",
    saved_search_id: int | None = None,
    include_ai_summary: bool = True,
    activated_at: datetime | None = None,
) -> ReportSchedule:
    return ReportSchedule(
        destination_id=destination.id,
        destination=destination,
        name=name,
        enabled=True,
        report_type="digest_15m",
        timezone="UTC",
        interval_minutes=15,
        saved_search_id=saved_search_id,
        source_groups=[],
        categories=[],
        severities=[],
        symbols=[],
        chains=[],
        minimum_trust_score=0,
        include_ai_summary=include_ai_summary,
        maximum_events=20,
        activated_at=activated_at,
    )


def _event(
    key: str,
    title: str,
    first_seen_at: datetime,
    *,
    category: str = "exchange_listing",
    severity: str = "normal",
    symbols: list[str] | None = None,
) -> Event:
    return Event(
        event_key=key,
        title=title,
        summary="摘要",
        category=category,
        status="confirmed",
        severity=severity,
        language="zh-CN",
        primary_url="https://example.com/news",
        published_at=first_seen_at,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        trust_score=90,
        confirmation_count=1,
        symbols=symbols or [],
        chains=[],
        entities=[],
        metadata_={},
    )
