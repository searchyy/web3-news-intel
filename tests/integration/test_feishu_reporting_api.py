from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import Delivery, Event, NotificationDestination
from app.db.session import get_session
from app.main import app


@pytest.mark.asyncio
async def test_admin_report_schedule_preview_and_test_send(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)
    monkeypatch.setattr(settings, "feishu_enabled", False)
    monkeypatch.setattr(settings, "feishu_send_enabled", False)
    now = datetime.now(UTC)
    destination = NotificationDestination(
        key="feishu-api-test",
        name="飞书 API 测试群",
        provider="feishu_app",
        enabled=True,
        status="active",
        chat_id="oc_api_test",
        chat_name="飞书 API 测试群",
        config={},
        activated_at=now - timedelta(hours=1),
    )
    historical_event = Event(
        event_key="api-report:history",
        title="启用前 BTC 上币快讯",
        summary="测试摘要",
        category="exchange_listing",
        status="confirmed",
        severity="high",
        language="zh-CN",
        primary_url="https://example.com/api-report",
        published_at=now - timedelta(minutes=3),
        first_seen_at=now - timedelta(minutes=3),
        last_seen_at=now - timedelta(minutes=3),
        trust_score=95,
        confirmation_count=1,
        symbols=["BTC"],
        chains=[],
        entities=[],
        metadata_={},
    )
    db_session.add_all([destination, historical_event])
    db_session.flush()

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/admin/auth/login",
                json={"username": "admin", "password": "password"},
            )
            csrf = login.json()["csrf_token"]
            created = await client.post(
                "/api/admin/report-schedules",
                json={
                    "destination_id": str(destination.id),
                    "name": "15 分钟测试汇报",
                    "report_type": "digest_15m",
                    "timezone": "UTC",
                    "interval_minutes": 15,
                    "include_ai_summary": True,
                    "maximum_events": 20,
                },
                headers={"x-csrf-token": csrf},
            )
            assert created.status_code == 200
            schedule_id = created.json()["id"]
            fresh_now = datetime.now(UTC)
            db_session.add(
                Event(
                    event_key="api-report:event",
                    title="BTC 上币快讯",
                    summary="测试摘要",
                    category="exchange_listing",
                    status="confirmed",
                    severity="high",
                    language="zh-CN",
                    primary_url="https://example.com/api-report",
                    published_at=fresh_now,
                    first_seen_at=fresh_now,
                    last_seen_at=fresh_now,
                    trust_score=95,
                    confirmation_count=1,
                    symbols=["BTC"],
                    chains=[],
                    entities=[],
                    metadata_={},
                )
            )
            db_session.flush()

            preview = await client.post(
                f"/api/admin/report-schedules/{schedule_id}/preview",
                headers={"x-csrf-token": csrf},
            )
            assert preview.status_code == 200
            assert preview.json()["event_count"] == 1
            assert "card" in preview.json()

            sent = await client.post(
                f"/api/admin/report-schedules/{schedule_id}/test-send",
                headers={"x-csrf-token": csrf},
            )
            assert sent.status_code == 200
            assert sent.json()["dry_run"] is True
            assert sent.json()["delivery_id"] is not None
            assert db_session.scalar(select(func.count(Delivery.id))) == 1
    finally:
        app.dependency_overrides.clear()
