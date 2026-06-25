from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import AIProviderConfig, AIRun, Event
from app.integrations.ai.runtime import get_ai_runtime_settings
from app.workers import tasks_ai, tasks_publish


def test_auto_ai_is_queued_in_ai_worker_not_run_inline(monkeypatch, db_session) -> None:
    event = _event("auto-ai-queued")
    config = _ai_config()
    db_session.add_all([event, config])
    db_session.commit()
    session_local = _sessionmaker_for(db_session)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_auto_process_enabled", True)
    monkeypatch.setattr(tasks_publish, "SessionLocal", session_local)

    def fake_apply_async(*, args, kwargs, queue, task_id):
        calls.append({"args": args, "kwargs": kwargs, "queue": queue, "task_id": task_id})

    monkeypatch.setattr(tasks_ai.summarize_event, "apply_async", fake_apply_async)

    status = tasks_publish._enqueue_auto_ai_if_allowed(event.id)

    assert status.startswith("queued:")
    assert len(calls) == 1
    call = calls[0]
    assert call["args"][1] == event.id
    assert call["kwargs"] == {"force": False, "auto": True}
    assert call["queue"] == get_ai_runtime_settings().queue_name
    with session_local() as session:
        job = session.scalar(select(AIRun).where(AIRun.event_ids == [event.id]))
        assert job is not None
        assert job.status == "queued"
        assert job.job_type == "summarize_event"
        assert job.task_id == call["task_id"]


def test_auto_ai_reuses_existing_active_job(monkeypatch, db_session) -> None:
    event = _event("auto-ai-existing")
    config = _ai_config()
    existing = AIRun(
        job_type="summarize_event",
        provider="deepseek",
        model="deepseek-chat",
        event_count=1,
        event_ids=[1],
        status="queued",
        queued_at=datetime.now(UTC),
        task_id="existing-task",
    )
    db_session.add_all([event, config])
    db_session.flush()
    existing.event_ids = [event.id]
    db_session.add(existing)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_auto_process_enabled", True)
    monkeypatch.setattr(tasks_publish, "SessionLocal", session_local)
    monkeypatch.setattr(
        tasks_ai.summarize_event,
        "apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue duplicate")),
    )

    status = tasks_publish._enqueue_auto_ai_if_allowed(event.id)

    assert status == f"queued:existing:{existing.id}"
    with session_local() as session:
        assert session.scalar(select(AIRun).where(AIRun.event_ids == [event.id])).id == existing.id


def test_auto_ai_skips_when_daily_token_budget_is_exhausted(monkeypatch, db_session) -> None:
    event = _event("auto-ai-budget")
    config = _ai_config()
    spent = AIRun(
        job_type="summarize_event",
        provider="deepseek",
        model="deepseek-chat",
        event_count=1,
        prompt_tokens=100000,
        completion_tokens=1,
        event_ids=[999],
        status="succeeded",
        queued_at=datetime.now(UTC),
    )
    db_session.add_all([event, config, spent])
    db_session.commit()
    session_local = _sessionmaker_for(db_session)

    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_auto_process_enabled", True)
    monkeypatch.setattr(tasks_publish, "SessionLocal", session_local)
    monkeypatch.setattr(
        tasks_ai.summarize_event,
        "apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue over budget")),
    )

    status = tasks_publish._enqueue_auto_ai_if_allowed(event.id)

    assert status == "skipped:token_budget_exceeded"
    with session_local() as session:
        assert session.scalar(select(AIRun).where(AIRun.event_ids == [event.id])) is None


def _event(key: str) -> Event:
    now = datetime.now(UTC)
    return Event(
        event_key=f"fixture:{key}",
        title="Major exchange launches new rewards campaign",
        summary="A large exchange opens a new trading and airdrop reward campaign.",
        category="exchange_activity",
        status="confirmed",
        severity="critical",
        language="en",
        primary_url=f"https://example.com/{key}",
        published_at=now,
        first_seen_at=now,
        last_seen_at=now,
        trust_score=95,
        confirmation_count=1,
        symbols=["BTC"],
        chains=["Bitcoin"],
    )


def _ai_config() -> AIProviderConfig:
    return AIProviderConfig(
        provider="deepseek",
        enabled=True,
        api_base="https://api.deepseek.com",
        api_key_ciphertext="encrypted-api-key",
        model="deepseek-chat",
        timeout_seconds=90,
        max_concurrency=1,
        max_tokens=1200,
        temperature=0.2,
        daily_token_budget=100000,
        daily_request_budget=100,
        auto_process_enabled=True,
        auto_minimum_severity="critical",
        config={"requests_per_minute": 60, "auto_minimum_priority_score": 85},
    )


def _sessionmaker_for(db_session):
    return sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
