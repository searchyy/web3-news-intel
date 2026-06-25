from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from argon2 import PasswordHasher

from app.api.routes import admin_api
from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import AIRun, Event, EventAIInsight
from app.db.session import get_session
from app.integrations.ai.service import AIService
from app.main import app
from app.schemas.ai import AIRuntimeStatus


@pytest.mark.asyncio
async def test_ai_summary_async_creates_queryable_job(monkeypatch, db_session) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event()
    db_session.add(event)
    db_session.flush()
    _configure_ai(monkeypatch, db_session)

    class FakeTask:
        def apply_async(self, *_args, **kwargs):
            return type("Result", (), {"id": kwargs["task_id"]})()

    monkeypatch.setattr(admin_api, "summarize_event_task", FakeTask())
    monkeypatch.setattr(admin_api, "_ai_runtime_status", lambda: _ready_async_runtime())
    monkeypatch.setenv("AI_EXECUTION_MODE", "async")

    try:
        response = await client.post(
            f"/api/admin/events/{event.id}/ai-summary",
            json={"force": False},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["queued"] is True
        assert payload["status"] == "queued"
        assert isinstance(payload["task_id"], str)
        assert len(payload["task_id"]) == 32
        assert payload["poll_url"] == f"/api/admin/ai/jobs/{payload['job_id']}"

        status_response = await client.get(payload["poll_url"])
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["job_id"] == payload["job_id"]
        assert status_payload["event_id"] == event.id
        assert status_payload["status"] == "queued"
        assert status_payload["task_id"] == payload["task_id"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    run = db_session.get(AIRun, payload["job_id"])
    assert run is not None
    assert run.status == "queued"
    assert run.task_id == payload["task_id"]
    assert run.event_ids == [event.id]
    assert run.error_sanitized is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("redis_available", "worker_available"),
    [(False, True), (True, False)],
)
async def test_ai_summary_async_runtime_unavailable_returns_503_without_sync_fallback(
    monkeypatch, db_session, redis_available: bool, worker_available: bool
) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event(event_key=f"ai:runtime:{redis_available}:{worker_available}")
    db_session.add(event)
    db_session.flush()
    _configure_ai(monkeypatch, db_session)

    class FailTask:
        def apply_async(self, *_args, **_kwargs):
            pytest.fail("async task should not be queued when runtime is unavailable")

    def fail_sync(*_args, **_kwargs):
        pytest.fail("async runtime failure must not fall back to sync execution")

    monkeypatch.setattr(admin_api, "summarize_event_task", FailTask())
    monkeypatch.setattr(admin_api, "summarize_event_sync", fail_sync)
    monkeypatch.setattr(
        admin_api,
        "_ai_runtime_status",
        lambda: AIRuntimeStatus(
            execution_mode="async",
            sync_allowed=True,
            redis_available=redis_available,
            worker_available=worker_available,
            queue_name="ai",
            status="degraded",
            error="runtime unavailable",
        ),
    )
    monkeypatch.setenv("AI_EXECUTION_MODE", "async")

    try:
        response = await client.post(
            f"/api/admin/events/{event.id}/ai-summary",
            json={"force": False},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 503
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_job_retry_and_cancel(monkeypatch, db_session) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event(event_key="ai:retry")
    db_session.add(event)
    db_session.flush()
    _configure_ai(monkeypatch, db_session)

    class FakeTask:
        def apply_async(self, *_args, **kwargs):
            return type("Result", (), {"id": kwargs["task_id"]})()

    cancelled: list[str] = []
    monkeypatch.setattr(admin_api, "summarize_event_task", FakeTask())
    monkeypatch.setattr(admin_api, "cancel_ai_task", cancelled.append)
    monkeypatch.setattr(admin_api, "_ai_runtime_status", lambda: _ready_async_runtime())
    monkeypatch.setenv("AI_EXECUTION_MODE", "async")

    try:
        created = await client.post(
            f"/api/admin/events/{event.id}/ai-summary",
            json={"force": False},
            headers={"x-csrf-token": csrf},
        )
        assert created.status_code == 202
        created_payload = created.json()
        job_id = created_payload["job_id"]
        run = db_session.get(AIRun, job_id)
        assert run is not None
        assert run.task_id == created_payload["task_id"]
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        db_session.commit()

        retried = await client.post(
            f"/api/admin/ai/jobs/{job_id}/retry",
            headers={"x-csrf-token": csrf},
        )
        assert retried.status_code == 200
        retried_payload = retried.json()
        assert isinstance(retried_payload["task_id"], str)
        assert retried_payload["task_id"] != created_payload["task_id"]

        duplicate_retry = await client.post(
            f"/api/admin/ai/jobs/{job_id}/retry",
            headers={"x-csrf-token": csrf},
        )
        assert duplicate_retry.status_code == 409

        cancelled_response = await client.post(
            f"/api/admin/ai/jobs/{job_id}/cancel",
            headers={"x-csrf-token": csrf},
        )
        assert cancelled_response.status_code == 200
        assert cancelled_response.json()["status"] == "cancelled"

        status_response = await client.get(f"/api/admin/ai/jobs/{job_id}")
        assert status_response.json()["status"] == "cancelled"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    run = db_session.get(AIRun, job_id)
    assert run is not None
    assert run.retry_count == 1
    assert cancelled == [retried_payload["task_id"]]


@pytest.mark.asyncio
async def test_ai_summary_sync_production_returns_503_without_running_sync(
    monkeypatch, db_session
) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event(event_key="ai:sync:production")
    db_session.add(event)
    db_session.flush()
    _configure_ai(monkeypatch, db_session)

    def fail_sync(*_args, **_kwargs):
        pytest.fail("sync execution should be forbidden outside local/development/test")

    monkeypatch.setenv("AI_EXECUTION_MODE", "sync")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(admin_api, "summarize_event_sync", fail_sync)

    try:
        response = await client.post(
            f"/api/admin/events/{event.id}/ai-summary",
            json={"force": False},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 503
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_summary_sync_development_returns_insight(monkeypatch, db_session) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event(event_key="ai:sync")
    db_session.add(event)
    db_session.flush()
    _configure_ai(monkeypatch, db_session)
    insight = _insight(event.id)
    db_session.add(insight)
    db_session.flush()

    monkeypatch.setenv("AI_EXECUTION_MODE", "sync")
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(admin_api, "summarize_event_sync", lambda *_args, **_kwargs: insight)

    try:
        response = await client.post(
            f"/api/admin/events/{event.id}/ai-summary",
            json={"force": False},
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["event_id"] == event.id
        assert payload["summary_zh"] == "测试摘要"
        assert "job_id" not in payload
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_runtime_status_reports_worker_failure(monkeypatch, db_session) -> None:
    csrf, client = await _logged_in_client(monkeypatch, db_session)
    monkeypatch.setenv("AI_EXECUTION_MODE", "async")
    monkeypatch.setattr(
        admin_api,
        "_ai_runtime_status",
        lambda: AIRuntimeStatus(
            execution_mode="async",
            sync_allowed=True,
            redis_available=True,
            worker_available=False,
            queue_name="ai",
            status="degraded",
            error="Celery Worker 未运行，AI 任务无法执行",
        ),
    )

    try:
        response = await client.get(
            "/api/admin/system/ai-runtime",
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_mode"] == "async"
        assert payload["redis_available"] is True
        assert payload["worker_available"] is False
        assert payload["status"] == "degraded"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_event_ai_insight_returns_null_when_missing(monkeypatch, db_session) -> None:
    _csrf, client = await _logged_in_client(monkeypatch, db_session)
    event = _event(event_key="ai:insight:missing")
    db_session.add(event)
    db_session.flush()

    try:
        response = await client.get(f"/api/admin/events/{event.id}/ai-insight")
        assert response.status_code == 200
        assert response.json() is None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_event_ai_insight_missing_event_returns_404(monkeypatch, db_session) -> None:
    _csrf, client = await _logged_in_client(monkeypatch, db_session)

    try:
        response = await client.get("/api/admin/events/999999/ai-insight")
        assert response.status_code == 404
        assert response.json()["detail"] == "事件不存在"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

async def _logged_in_client(monkeypatch, db_session) -> tuple[str, httpx.AsyncClient]:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    response = await client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"]), client


def _configure_ai(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    AIService(db_session).save_provider_config(
        {
            "enabled": True,
            "api_key": "sk-unit",
            "model": "deepseek-flash",
            "daily_token_budget": 1000,
            "daily_request_budget": 100,
        }
    )
    db_session.flush()


def _ready_async_runtime() -> AIRuntimeStatus:
    return AIRuntimeStatus(
        execution_mode="async",
        sync_allowed=True,
        redis_available=True,
        worker_available=True,
        queue_name="ai",
        status="ready",
    )


def _event(*, event_key: str = "ai:job") -> Event:
    return Event(
        event_key=event_key,
        title="BTC market event",
        summary="BTC summary",
        category="market",
        status="confirmed",
        severity="high",
        language="en",
        primary_url="https://example.com/btc",
        published_at=datetime.now(UTC),
        trust_score=80,
        confirmation_count=1,
        symbols=["BTC"],
        chains=["Bitcoin"],
        entities=[],
        metadata_={},
    )


def _insight(event_id: int) -> EventAIInsight:
    return EventAIInsight(
        event_id=event_id,
        provider="deepseek",
        model="deepseek-flash",
        prompt_version="v1",
        input_hash="hash",
        summary_zh="测试摘要",
        headline_zh="测试标题",
        key_facts=[],
        entities=[],
        symbols=["BTC"],
        chains=["Bitcoin"],
        event_type="market",
        importance_score=70,
        risk_level="medium",
        sentiment="neutral",
        market_impact="不确定",
        facts=[],
        inferences=[],
        confidence=0.7,
        source_event_ids=[str(event_id)],
        source_urls=["https://example.com/btc"],
        prompt_tokens=10,
        completion_tokens=8,
        generated_at=datetime.now(UTC),
        status="succeeded",
    )
