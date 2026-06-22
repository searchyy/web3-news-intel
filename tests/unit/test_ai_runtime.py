from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import AIRun
from app.integrations.ai.runtime import (
    AIRuntimeHealth,
    AIRuntimeSettings,
    AIRuntimeStatus,
    AIRuntimeTimeoutError,
    AIRuntimeUnavailableError,
    AISyncModeForbiddenError,
    ensure_async_runtime_available,
    get_ai_runtime_settings,
    validate_sync_execution_allowed,
)
from app.integrations.ai.service import AIService, mark_stale_ai_runs, summarize_event_sync
from app.workers.celery_app import celery_app
from app.workers.tasks_ai import (
    _claim_batch_success,
    _claim_failed,
    _claim_retrying,
    _claim_started,
    summarize_event,
    summarize_event_batch,
)


def test_ai_runtime_settings_default_to_async_ai_queue(monkeypatch) -> None:
    for name in (
        "AI_EXECUTION_MODE",
        "AI_SYNC_ALLOWED_ENVIRONMENTS",
        "AI_SYNC_TIMEOUT_SECONDS",
        "AI_QUEUE_NAME",
        "AI_JOB_STUCK_SECONDS",
        "AI_JOB_TIMEOUT_SECONDS",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "ai_execution_mode", "async")
    monkeypatch.setattr(settings, "ai_sync_allowed_environments", "local,development,test")
    monkeypatch.setattr(settings, "ai_sync_timeout_seconds", 60)
    monkeypatch.setattr(settings, "ai_queue_name", "ai")
    monkeypatch.setattr(settings, "ai_job_stuck_seconds", 10)
    monkeypatch.setattr(settings, "ai_job_timeout_seconds", 90)

    runtime_settings = get_ai_runtime_settings()

    assert runtime_settings.execution_mode == "async"
    assert runtime_settings.queue_name == "ai"
    assert runtime_settings.sync_timeout_seconds == 60
    assert runtime_settings.job_stuck_seconds == 10
    assert runtime_settings.job_timeout_seconds == 90


def test_sync_mode_allowed_only_for_configured_non_production_environments() -> None:
    local_settings = _runtime_settings(execution_mode="sync", app_env="local")
    assert validate_sync_execution_allowed(local_settings) is local_settings

    production_settings = _runtime_settings(execution_mode="sync", app_env="production")
    with pytest.raises(AISyncModeForbiddenError) as exc_info:
        validate_sync_execution_allowed(production_settings)
    assert "production/staging 环境禁止使用 AI 同步执行模式" in str(exc_info.value)

    qa_settings = _runtime_settings(execution_mode="sync", app_env="qa")
    with pytest.raises(AISyncModeForbiddenError) as qa_exc:
        validate_sync_execution_allowed(qa_settings)
    assert "当前环境 qa 不允许 AI 同步执行" in str(qa_exc.value)


def test_async_runtime_reports_redis_unavailable_without_worker_wait() -> None:
    with pytest.raises(AIRuntimeUnavailableError) as exc_info:
        ensure_async_runtime_available(
            celery_app,
            _runtime_settings(execution_mode="async"),
            redis_health=AIRuntimeHealth(
                ok=False,
                status="redis_unavailable",
                detail="Redis 不可用",
            ),
            worker_health=AIRuntimeHealth(ok=True, status="worker_ok"),
        )

    assert "Redis 不可用" in str(exc_info.value)


def test_async_runtime_reports_worker_unavailable_after_redis_ok() -> None:
    with pytest.raises(AIRuntimeUnavailableError) as exc_info:
        ensure_async_runtime_available(
            celery_app,
            _runtime_settings(execution_mode="async"),
            redis_health=AIRuntimeHealth(ok=True, status="redis_ok"),
            worker_health=AIRuntimeHealth(
                ok=False,
                status="worker_unavailable",
                detail="Celery Worker 未运行或心跳已过期",
            ),
        )

    assert "Celery Worker 未运行或心跳已过期" in str(exc_info.value)


def test_worker_health_requires_ai_queue() -> None:
    from app.integrations.ai.runtime import check_worker_health

    class Inspector:
        def ping(self):
            return {"worker-a": {"ok": "pong"}}

        def active_queues(self):
            return {}

    class Control:
        def inspect(self, timeout):
            return Inspector()

    class FakeCelery:
        control = Control()

    health = check_worker_health(FakeCelery(), queue_name="ai")

    assert health.ok is False
    assert health.status == "worker_queue_unknown"


def test_sync_runtime_skips_async_redis_and_worker_checks() -> None:
    health = ensure_async_runtime_available(
        celery_app,
        _runtime_settings(execution_mode="sync"),
        redis_health=AIRuntimeHealth(ok=False, status="redis_unavailable"),
        worker_health=AIRuntimeHealth(ok=False, status="worker_unavailable"),
    )

    assert health.ok is True
    assert health.status == "sync_mode"


def test_sync_runtime_status_requires_allowed_environment() -> None:
    status = AIRuntimeStatus(
        execution_mode="sync",
        sync_allowed=False,
        redis_ok=False,
        worker_ok=False,
        queue_name="ai",
    )

    assert status.ready is False


def test_ai_celery_tasks_use_ai_queue_and_runtime_time_limits() -> None:
    runtime_settings = get_ai_runtime_settings()

    assert celery_app.conf.task_routes["app.workers.tasks_ai.*"]["queue"] == "ai"
    assert summarize_event.queue == runtime_settings.queue_name
    assert summarize_event_batch.queue == runtime_settings.queue_name
    assert summarize_event.soft_time_limit == runtime_settings.task_soft_time_limit_seconds
    assert summarize_event.time_limit == runtime_settings.job_timeout_seconds


def test_ai_worker_started_claim_rejects_stale_task_id(db_session) -> None:
    run = AIRun(
        job_type="summarize_event",
        provider="deepseek",
        model="deepseek-chat",
        event_count=1,
        event_ids=[1],
        status="queued",
        queued_at=utc_now(),
        task_id="current-task",
    )
    db_session.add(run)
    db_session.flush()

    assert _claim_started(db_session, run, request_id="old-task", worker_name="worker-a") is False
    assert run.status == "queued"
    assert run.started_at is None

    assert (
        _claim_started(db_session, run, request_id="current-task", worker_name="worker-a") is True
    )
    assert run.status == "started"
    assert run.worker_name == "worker-a"


def test_ai_stale_sweep_fails_active_jobs_without_touching_terminal_statuses(
    monkeypatch, db_session
) -> None:
    monkeypatch.setenv("AI_JOB_STUCK_SECONDS", "5")
    monkeypatch.setenv("AI_JOB_TIMEOUT_SECONDS", "10")
    old_queued_at = utc_now() - timedelta(seconds=30)
    old_started_at = utc_now() - timedelta(seconds=30)
    active_runs = [
        _ai_run(status="queued", queued_at=old_queued_at),
        _ai_run(status="started", queued_at=old_queued_at, started_at=old_started_at),
        _ai_run(status="retrying", queued_at=old_queued_at),
    ]
    terminal_runs = [
        _ai_run(status="succeeded", queued_at=old_queued_at, started_at=old_started_at),
        _ai_run(status="failed", queued_at=old_queued_at, started_at=old_started_at),
        _ai_run(status="cancelled", queued_at=old_queued_at, started_at=old_started_at),
    ]
    db_session.add_all(active_runs + terminal_runs)
    db_session.flush()

    changed = mark_stale_ai_runs(db_session)

    assert changed == 3
    assert [run.status for run in active_runs] == ["failed", "failed", "failed"]
    assert [run.error_code for run in active_runs] == [
        "ai_job_timeout",
        "ai_job_timeout",
        "ai_job_timeout",
    ]
    assert [run.status for run in terminal_runs] == ["succeeded", "failed", "cancelled"]


def test_ai_worker_terminal_claims_are_idempotent(db_session) -> None:
    terminal_runs = [
        _ai_run(status="succeeded", task_id="task-succeeded"),
        _ai_run(status="failed", task_id="task-failed"),
        _ai_run(status="cancelled", task_id="task-cancelled"),
    ]
    db_session.add_all(terminal_runs)
    db_session.flush()

    assert (
        _claim_retrying(
            db_session,
            terminal_runs[0],
            RuntimeError("retry later"),
            retry_count=1,
            request_id="task-succeeded",
        )
        is False
    )
    assert (
        _claim_failed(
            db_session,
            terminal_runs[1],
            RuntimeError("late failure"),
            request_id="task-failed",
        )
        is False
    )
    assert _claim_batch_success(db_session, terminal_runs[2], request_id="task-cancelled") is False

    assert [run.status for run in terminal_runs] == ["succeeded", "failed", "cancelled"]


def test_summarize_event_sync_raises_chinese_timeout(monkeypatch, db_session) -> None:
    async def slow_summary(self: AIService, event_id: int, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(AIService, "summarize_event", slow_summary)

    with pytest.raises(AIRuntimeTimeoutError) as exc_info:
        summarize_event_sync(db_session, 1, timeout_seconds=0)

    assert "AI 同步生成超时" in str(exc_info.value)


def _ai_run(
    *,
    status: str,
    queued_at=None,
    started_at=None,
    task_id: str | None = None,
) -> AIRun:
    return AIRun(
        job_type="summarize_event",
        provider="deepseek",
        model="deepseek-chat",
        event_count=1,
        event_ids=[1],
        status=status,
        queued_at=queued_at or utc_now(),
        started_at=started_at,
        task_id=task_id,
    )


def _runtime_settings(
    *,
    execution_mode: str = "async",
    app_env: str = "local",
) -> AIRuntimeSettings:
    return AIRuntimeSettings(
        execution_mode=execution_mode,  # type: ignore[arg-type]
        sync_allowed_environments={"local", "development", "test"},
        sync_timeout_seconds=60,
        queue_name="ai",
        job_stuck_seconds=10,
        job_timeout_seconds=90,
        broker_url="redis://localhost:6379/0",
        result_backend="redis://localhost:6379/0",
        app_env=app_env,
    )
