from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import (
    Delivery,
    Event,
    EventSource,
    NotificationDestination,
    ReportSchedule,
    SavedSearch,
    Source,
)
from app.integrations.feishu.report_cards import REPORT_TOPIC_DEFINITIONS, _event_matches_topic
from app.integrations.feishu.reporting import FeishuReportService


@pytest.fixture(autouse=True)
def disable_real_feishu_send(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_enabled", False)
    monkeypatch.setattr(settings, "feishu_send_enabled", False)


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



def test_report_summary_groups_daily_topics(db_session) -> None:
    now = utc_now()
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    schedule = _schedule(
        destination,
        include_ai_summary=True,
        activated_at=now - timedelta(hours=1),
    )
    events = [
        _event(
            "event:listing",
            "Binance will list TEST spot trading",
            now - timedelta(minutes=12),
            category="listing",
            symbols=["TEST"],
        ),
        _event(
            "event:liquidation",
            "BTC 多空爆仓金额快速上升",
            now - timedelta(minutes=15),
            category="market",
            symbols=["BTC"],
        ),
        _event(
            "event:hack",
            "某协议遭黑客攻击并出现资金被盗",
            now - timedelta(minutes=10),
            category="hack_security",
            severity="high",
        ),
    ]
    db_session.add_all([schedule, *events])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)

    assert "主题总结" in preview.summary_zh
    assert "CEX 活动" in preview.summary_zh
    assert "市场情绪/爆仓" in preview.summary_zh
    assert "安全黑客" in preview.summary_zh
    assert "确定性规则模板" in preview.summary_zh

def test_report_topic_requires_category_or_keyword_not_source_group_only() -> None:
    event = _event(
        "event:generic-media",
        "General ecosystem interview without trading signal",
        datetime(2026, 6, 24, 8, 0, tzinfo=UTC),
        category="deep_article",
    )
    source = Source(
        id=1,
        key="media",
        name="Media",
        source_type="tier1_media",
        adapter="rss",
        url="https://example.com",
        canonical_url="https://example.com",
        category="media",
        trust_score=75,
        poll_seconds=30,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={"source_group": "media_en"},
    )
    event.sources = [
        EventSource(
            event_id=1,
            source_id=1,
            url="https://example.com/1",
            source_score=75,
            source=source,
        )
    ]

    assert not _event_matches_topic(event, REPORT_TOPIC_DEFINITIONS[2])


def test_daily_evening_report_uses_morning_to_evening_window(db_session) -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    zone = ZoneInfo("Asia/Taipei")
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    schedule = _schedule(
        destination,
        report_type="daily_evening",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=18,
        minute=0,
        maximum_events=5,
        activated_at=now - timedelta(days=1),
    )
    before_window = _event(
        "event:before-daily-window",
        "Binance will list OLD",
        datetime(2026, 6, 24, 0, 30, tzinfo=UTC),
        category="listing",
        symbols=["OLD"],
    )
    inside_window = _event(
        "event:inside-daily-window",
        "Binance will list NEW spot trading",
        datetime(2026, 6, 24, 1, 30, tzinfo=UTC),
        category="listing",
        symbols=["NEW"],
    )
    db_session.add_all([schedule, before_window, inside_window])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)

    local_start = preview.window_start.astimezone(zone)
    assert (local_start.hour, local_start.minute) == (9, 0)
    assert [event.event_key for event in preview.events] == ["event:inside-daily-window"]


def test_daily_pair_windows_use_counterpart_schedule_time(db_session) -> None:
    destination = _destination(datetime(2026, 6, 24, 0, 0, tzinfo=UTC))
    db_session.add(destination)
    db_session.flush()
    morning = _schedule(
        destination,
        report_type="daily_morning",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=8,
        minute=30,
        activated_at=datetime(2026, 6, 23, 0, 0, tzinfo=UTC),
    )
    evening = _schedule(
        destination,
        report_type="daily_evening",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=20,
        minute=15,
        activated_at=datetime(2026, 6, 23, 0, 0, tzinfo=UTC),
    )
    db_session.add_all([morning, evening])
    db_session.flush()
    service = FeishuReportService(db_session)
    zone = ZoneInfo("Asia/Taipei")

    evening_start = service.default_window_start(
        evening, datetime(2026, 6, 24, 12, 15, tzinfo=UTC)
    ).astimezone(zone)
    morning_start = service.default_window_start(
        morning, datetime(2026, 6, 25, 0, 30, tzinfo=UTC)
    ).astimezone(zone)

    assert (evening_start.year, evening_start.month, evening_start.day) == (2026, 6, 24)
    assert (evening_start.hour, evening_start.minute) == (8, 30)
    assert (morning_start.year, morning_start.month, morning_start.day) == (2026, 6, 24)
    assert (morning_start.hour, morning_start.minute) == (20, 15)


def test_due_daily_report_ignores_own_last_window_end(db_session) -> None:
    now = datetime(2026, 6, 24, 12, 15, tzinfo=UTC)
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    morning = _schedule(
        destination,
        report_type="daily_morning",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=8,
        minute=30,
        activated_at=now - timedelta(days=2),
    )
    evening = _schedule(
        destination,
        report_type="daily_evening",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=20,
        minute=15,
        maximum_events=5,
        activated_at=now - timedelta(days=2),
    )
    evening.next_run_at = now
    evening.last_window_end = now - timedelta(days=1)
    inside = _event(
        "event:paired-window-inside",
        "Binance will list PAIR spot trading",
        datetime(2026, 6, 24, 1, 0, tzinfo=UTC),
        category="listing",
        symbols=["PAIR"],
    )
    outside = _event(
        "event:paired-window-outside",
        "Binance will list OLD spot trading",
        datetime(2026, 6, 24, 0, 0, tzinfo=UTC),
        category="listing",
        symbols=["OLD"],
    )
    db_session.add_all([morning, evening, inside, outside])
    db_session.flush()

    outcome = FeishuReportService(db_session).run_due_schedule(evening, now=now)

    assert outcome is not None
    assert [event.event_key for event in outcome.preview.events] == ["event:paired-window-inside"]
    local_start = evening.last_window_start.astimezone(ZoneInfo("Asia/Taipei"))
    assert (local_start.hour, local_start.minute) == (8, 30)


def test_daily_report_summary_is_briefing_not_news_dump(db_session) -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    schedule = _schedule(
        destination,
        report_type="daily_evening",
        timezone="Asia/Taipei",
        interval_minutes=None,
        hour=18,
        minute=0,
        maximum_events=5,
        activated_at=now - timedelta(days=1),
    )
    events = [
        _event(
            f"event:daily-{index}",
            title,
            now - timedelta(minutes=index + 1),
            category=category,
            severity=severity,
            symbols=symbols,
        )
        for index, (title, category, severity, symbols) in enumerate(
            [
                ("Binance will list ALPHA spot trading", "listing", "normal", ["ALPHA"]),
                (
                    "Aster launches points campaign testnet quests",
                    "project_update",
                    "normal",
                    ["ASTER"],
                ),
                (
                    "Smart money whale accumulates HYPE on Hyperliquid",
                    "onchain",
                    "normal",
                    ["HYPE"],
                ),
                ("BTC long liquidation and funding rate spike", "market", "normal", ["BTC"]),
                ("Protocol suffers hack and stolen funds", "hack_security", "high", []),
                ("Backpack announces trading competition rewards", "product", "normal", ["BP"]),
                ("Fed rate cut expectations move BTC", "market", "normal", ["BTC"]),
                ("Token unlock schedule updated", "token_unlock", "normal", ["TOKEN"]),
            ]
        )
    ]
    db_session.add_all([schedule, *events])
    db_session.flush()

    preview = FeishuReportService(db_session).preview(schedule, now=now)
    card_text = json.dumps(preview.card, ensure_ascii=False)

    assert "结论：早报到晚报" in preview.summary_zh
    assert "本窗口发生的重点" in preview.summary_zh
    assert "综合判断" in preview.summary_zh
    assert "建议跟进" in preview.summary_zh
    assert "AI重点" not in preview.summary_zh
    assert "重点事件参考（非完整新闻列表）" in card_text
    assert card_text.count("重点分：") == 5
    assert preview.omitted_count == 3


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


def test_report_send_exception_marks_delivery_failed(db_session, monkeypatch) -> None:
    now = datetime(2026, 6, 21, 9, 30, tzinfo=UTC)
    destination = _destination(now)
    db_session.add(destination)
    db_session.flush()
    event = _event("event:report-failure", "汇报失败事件", now - timedelta(minutes=3))
    schedule = _schedule(destination, activated_at=now - timedelta(hours=1))
    db_session.add_all([event, schedule])
    db_session.flush()
    service = FeishuReportService(db_session)

    async def fail_send(*_args, **_kwargs):
        raise RuntimeError("webhook token secret leaked")

    monkeypatch.setattr(service, "_send", fail_send)

    outcome = asyncio.run(
        service.send_report_for_window(
            schedule,
            window_start=now - timedelta(minutes=15),
            window_end=now,
        )
    )

    assert outcome.status == "failed"
    delivery = db_session.scalar(select(Delivery))
    assert delivery is not None
    assert delivery.status == "failed"
    assert delivery.last_error == "Feishu delivery failed"


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
    report_type: str = "digest_15m",
    timezone: str = "UTC",
    interval_minutes: int | None = 15,
    hour: int | None = None,
    minute: int | None = None,
    maximum_events: int = 20,
) -> ReportSchedule:
    return ReportSchedule(
        destination_id=destination.id,
        destination=destination,
        name=name,
        enabled=True,
        report_type=report_type,
        timezone=timezone,
        interval_minutes=interval_minutes,
        hour=hour,
        minute=minute,
        saved_search_id=saved_search_id,
        source_groups=[],
        categories=[],
        severities=[],
        symbols=[],
        chains=[],
        minimum_trust_score=0,
        include_ai_summary=include_ai_summary,
        maximum_events=maximum_events,
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
