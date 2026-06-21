from __future__ import annotations

from app.db.session import SessionLocal
from app.integrations.ai.deepseek.errors import AIProviderError
from app.integrations.ai.service import summarize_event_sync
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.tasks_ai.summarize_event",
    bind=True,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def summarize_event(
    self,
    event_id: int, *, force: bool = False, auto: bool = False
) -> dict[str, int | str]:
    try:
        with SessionLocal() as session:
            insight = summarize_event_sync(session, event_id, force=force, auto=auto)
            session.commit()
            return {"status": insight.status, "event_id": event_id, "insight_id": insight.id}
    except AIProviderError as exc:
        if getattr(exc, "retryable", False):
            countdown = int(getattr(exc, "retry_after_seconds", None) or 30)
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise


@celery_app.task(name="app.workers.tasks_ai.summarize_event_batch", bind=True, max_retries=3)
def summarize_event_batch(
    self,
    event_ids: list[int], *, force: bool = False, auto: bool = False
) -> dict[str, int]:
    completed = 0
    try:
        with SessionLocal() as session:
            for event_id in event_ids:
                summarize_event_sync(session, int(event_id), force=force, auto=auto)
                completed += 1
            session.commit()
    except AIProviderError as exc:
        if getattr(exc, "retryable", False):
            countdown = int(getattr(exc, "retry_after_seconds", None) or 30)
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise
    return {"completed": completed}


@celery_app.task(name="app.workers.tasks_ai.build_periodic_digest")
def build_periodic_digest(saved_search_id: int | None = None) -> dict[str, int | None | str]:
    return {"status": "queued", "saved_search_id": saved_search_id}


@celery_app.task(name="app.workers.tasks_ai.build_daily_report")
def build_daily_report(saved_search_id: int | None = None) -> dict[str, int | None | str]:
    return {"status": "queued", "saved_search_id": saved_search_id}


def cancel_ai_task(task_id: str) -> None:
    celery_app.control.revoke(task_id, terminate=False)
