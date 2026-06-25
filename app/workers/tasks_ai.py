from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.core.time import ensure_utc, utc_now
from app.db.models import AIRun
from app.db.session import SessionLocal
from app.integrations.ai.deepseek.errors import AIProviderError
from app.integrations.ai.runtime import get_ai_runtime_settings
from app.integrations.ai.service import (
    ACTIVE_JOB_STATUSES,
    AIJobCancelledError,
    AIJobStoppedError,
    mark_stale_ai_runs,
    sanitize_error,
    summarize_event_sync,
)
from app.workers.celery_app import celery_app

_runtime_settings = get_ai_runtime_settings()


@celery_app.task(
    name="app.workers.tasks_ai.summarize_event",
    bind=True,
    queue=_runtime_settings.queue_name,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=_runtime_settings.task_soft_time_limit_seconds,
    time_limit=_runtime_settings.job_timeout_seconds,
)
def summarize_event(
    self,
    job_id: int,
    event_id: int,
    *,
    force: bool = False,
    auto: bool = False,
) -> dict[str, int | str | None]:
    worker_name = str(getattr(self.request, "hostname", "") or "celery-worker")
    request_id = str(getattr(self.request, "id", "") or "")
    with SessionLocal() as session:
        job = _job_or_error(session, job_id, expected_type="summarize_event")
        if _is_stale_celery_task(job, request_id):
            return {"status": "stale_task", "event_id": event_id, "job_id": job_id}
        if job.status in {"cancelled", "failed", "succeeded"}:
            return {"status": job.status, "event_id": event_id, "job_id": job_id}
        if not _claim_started(session, job, request_id=request_id, worker_name=worker_name):
            session.commit()
            return {"status": job.status, "event_id": event_id, "job_id": job_id}
        session.commit()
        try:
            insight = summarize_event_sync(
                session,
                event_id,
                force=force,
                auto=auto,
                timeout_seconds=_runtime_settings.job_timeout_seconds,
                run=job,
                worker_name=worker_name,
                job_type="summarize_event",
            )
            session.commit()
            return {
                "status": job.status,
                "event_id": event_id,
                "job_id": job_id,
                "insight_id": insight.id,
            }
        except AIJobCancelledError:
            session.rollback()
            job = _job_or_error(session, job_id, expected_type="summarize_event")
            return {"status": job.status, "event_id": event_id, "job_id": job_id}
        except AIJobStoppedError:
            session.rollback()
            job = _job_or_error(session, job_id, expected_type="summarize_event")
            return {"status": job.status, "event_id": event_id, "job_id": job_id}
        except AIProviderError as exc:
            if getattr(exc, "retryable", False):
                if not _claim_retrying(
                    session,
                    job,
                    exc,
                    retry_count=int(getattr(self.request, "retries", 0)) + 1,
                    request_id=request_id,
                ):
                    session.commit()
                    return {"status": job.status, "event_id": event_id, "job_id": job_id}
                session.commit()
                countdown = int(getattr(exc, "retry_after_seconds", None) or 30)
                raise self.retry(exc=exc, countdown=countdown) from exc
            if not _claim_failed(session, job, exc, request_id=request_id):
                session.commit()
                return {"status": job.status, "event_id": event_id, "job_id": job_id}
            session.commit()
            return {"status": "failed", "event_id": event_id, "job_id": job_id}
        except Exception as exc:
            if not _claim_failed(session, job, exc, request_id=request_id):
                session.commit()
                return {"status": job.status, "event_id": event_id, "job_id": job_id}
            session.commit()
            raise


@celery_app.task(
    name="app.workers.tasks_ai.summarize_event_batch",
    bind=True,
    queue=_runtime_settings.queue_name,
    max_retries=3,
    soft_time_limit=_runtime_settings.task_soft_time_limit_seconds,
    time_limit=_runtime_settings.job_timeout_seconds,
)
def summarize_event_batch(
    self,
    job_id: int,
    event_ids: list[int],
    *,
    force: bool = False,
    auto: bool = False,
) -> dict[str, int | str | list[int]]:
    worker_name = str(getattr(self.request, "hostname", "") or "celery-worker")
    request_id = str(getattr(self.request, "id", "") or "")
    completed = 0
    with SessionLocal() as session:
        job = _job_or_error(session, job_id, expected_type="summarize_event_batch")
        if _is_stale_celery_task(job, request_id):
            return {"status": "stale_task", "job_id": job_id, "completed": completed}
        if job.status in {"cancelled", "failed", "succeeded"}:
            return {"status": job.status, "job_id": job_id, "completed": completed}
        if not _claim_started(session, job, request_id=request_id, worker_name=worker_name):
            session.commit()
            return {"status": job.status, "job_id": job_id, "completed": completed}
        session.commit()
        try:
            for event_id in event_ids:
                session.refresh(job)
                if job.status == "cancelled":
                    job.finished_at = job.finished_at or utc_now()
                    session.commit()
                    return {"status": "cancelled", "job_id": job_id, "completed": completed}
                summarize_event_sync(
                    session,
                    int(event_id),
                    force=force,
                    auto=auto,
                    timeout_seconds=_runtime_settings.job_timeout_seconds,
                    run=job,
                    worker_name=worker_name,
                    job_type="summarize_event_batch",
                )
                completed += 1
            if not _claim_batch_success(session, job, request_id=request_id):
                session.refresh(job)
                session.commit()
                return {"status": job.status, "job_id": job_id, "completed": completed}
            session.commit()
        except AIJobCancelledError:
            session.rollback()
            job = _job_or_error(session, job_id, expected_type="summarize_event_batch")
            return {"status": job.status, "job_id": job_id, "completed": completed}
        except AIJobStoppedError:
            session.rollback()
            job = _job_or_error(session, job_id, expected_type="summarize_event_batch")
            return {"status": job.status, "job_id": job_id, "completed": completed}
        except AIProviderError as exc:
            if getattr(exc, "retryable", False):
                if not _claim_retrying(
                    session,
                    job,
                    exc,
                    retry_count=int(getattr(self.request, "retries", 0)) + 1,
                    request_id=request_id,
                ):
                    session.commit()
                    return {"status": job.status, "job_id": job_id, "completed": completed}
                session.commit()
                countdown = int(getattr(exc, "retry_after_seconds", None) or 30)
                raise self.retry(exc=exc, countdown=countdown) from exc
            if not _claim_failed(session, job, exc, request_id=request_id):
                session.commit()
                return {"status": job.status, "job_id": job_id, "completed": completed}
            session.commit()
            return {"status": "failed", "job_id": job_id, "completed": completed}
        except Exception as exc:
            if not _claim_failed(session, job, exc, request_id=request_id):
                session.commit()
                return {"status": job.status, "job_id": job_id, "completed": completed}
            session.commit()
            raise
    return {"status": "succeeded", "job_id": job_id, "completed": completed}


@celery_app.task(
    name="app.workers.tasks_ai.build_periodic_digest",
    queue=_runtime_settings.queue_name,
)
def build_periodic_digest(saved_search_id: int | None = None) -> dict[str, int | None | str]:
    return {"status": "queued", "saved_search_id": saved_search_id}


@celery_app.task(
    name="app.workers.tasks_ai.build_daily_report",
    queue=_runtime_settings.queue_name,
)
def build_daily_report(saved_search_id: int | None = None) -> dict[str, int | None | str]:
    return {"status": "queued", "saved_search_id": saved_search_id}


@celery_app.task(
    name="app.workers.tasks_ai.mark_stale_ai_jobs",
    queue=_runtime_settings.queue_name,
)
def mark_stale_ai_jobs() -> dict[str, int | str]:
    with SessionLocal() as session:
        changed = mark_stale_ai_runs(session)
        session.commit()
    return {"status": "succeeded", "changed": changed}


def cancel_ai_task(task_id: str) -> None:
    celery_app.control.revoke(task_id, terminate=False)


def _job_or_error(session: Any, job_id: int, *, expected_type: str) -> AIRun:
    job = session.get(AIRun, job_id)
    if job is None or job.job_type != expected_type:
        raise ValueError(f"AI job {job_id} not found")
    return job


def _is_stale_celery_task(job: AIRun, request_id: str | None) -> bool:
    return bool(job.task_id and request_id and job.task_id != str(request_id))


def _claim_started(
    session: Any,
    job: AIRun,
    *,
    request_id: str,
    worker_name: str,
) -> bool:
    started_at = utc_now()
    values: dict[str, Any] = {
        "status": "started",
        "started_at": started_at,
        "worker_name": worker_name,
    }
    queued_at = ensure_utc(job.queued_at)
    if queued_at:
        values["queue_wait_ms"] = max(0, int((started_at - queued_at).total_seconds() * 1000))
    result = session.execute(
        update(AIRun)
        .where(*_current_task_filters(job, request_id))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.refresh(job)
    return result.rowcount == 1


def _claim_retrying(
    session: Any,
    job: AIRun,
    exc: BaseException,
    *,
    retry_count: int,
    request_id: str,
) -> bool:
    error_message = sanitize_error(exc)
    result = session.execute(
        update(AIRun)
        .where(*_current_task_filters(job, request_id))
        .values(
            status="retrying",
            retry_count=max(job.retry_count or 0, retry_count),
            error_code=getattr(exc, "error_code", "ai_retrying"),
            error_sanitized=error_message,
            error_message_sanitized=error_message,
        )
        .execution_options(synchronize_session=False)
    )
    session.refresh(job)
    return result.rowcount == 1


def _claim_failed(
    session: Any,
    job: AIRun,
    exc: BaseException,
    *,
    request_id: str,
) -> bool:
    finished_at = utc_now()
    started = ensure_utc(job.queued_at or job.started_at or job.created_at)
    values: dict[str, Any] = {
        "status": "failed",
        "error_code": getattr(exc, "error_code", "ai_task_failed"),
        "error_sanitized": sanitize_error(exc),
        "finished_at": finished_at,
    }
    values["error_message_sanitized"] = values["error_sanitized"]
    if started:
        total_latency_ms = max(0, int((finished_at - started).total_seconds() * 1000))
        values["total_latency_ms"] = total_latency_ms
        values["latency_ms"] = total_latency_ms
    result = session.execute(
        update(AIRun)
        .where(*_current_task_filters(job, request_id))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.refresh(job)
    return result.rowcount == 1


def _claim_batch_success(session: Any, job: AIRun, *, request_id: str) -> bool:
    finished_at = utc_now()
    started = ensure_utc(job.queued_at or job.started_at or job.created_at)
    total_latency_ms = (
        max(0, int((finished_at - started).total_seconds() * 1000)) if started else None
    )
    values: dict[str, Any] = {"status": "succeeded", "finished_at": finished_at}
    if total_latency_ms is not None:
        values["total_latency_ms"] = total_latency_ms
        values["latency_ms"] = total_latency_ms
    result = session.execute(
        update(AIRun)
        .where(*_current_task_filters(job, request_id))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    session.refresh(job)
    return True


def _current_task_filters(job: AIRun, request_id: str) -> tuple[Any, ...]:
    filters: list[Any] = [AIRun.id == job.id, AIRun.status.in_(ACTIVE_JOB_STATUSES)]
    if job.task_id:
        filters.append(AIRun.task_id == request_id)
    return tuple(filters)
