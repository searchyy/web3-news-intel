from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Event, EventSource
from app.db.session import SessionLocal
from app.pipeline.scoring import ScoringService
from app.workers.celery_app import CELERY_PIPELINE_PRIORITY, CELERY_PIPELINE_QUEUE, celery_app


@celery_app.task(
    name="app.workers.tasks_score.score_event",
    queue=CELERY_PIPELINE_QUEUE,
    priority=CELERY_PIPELINE_PRIORITY,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def score_event(event_id: int) -> dict[str, int | str]:
    with SessionLocal() as session:
        event = session.scalar(select(Event).where(Event.id == event_id))
        if event is None:
            return {"status": "not_found", "event_id": event_id}
        event_sources = list(
            session.scalars(
                select(EventSource)
                .options(selectinload(EventSource.source))
                .where(EventSource.event_id == event.id)
                .order_by(EventSource.id)
            )
        )
        result = ScoringService().score(event, event_sources)
        event.trust_score = result.trust_score
        event.status = result.status
        event.severity = result.severity
        event.confirmation_count = result.confirmation_count
        metadata = dict(event.metadata_ or {})
        metadata["score_reasons"] = result.reasons
        metadata["priority_score"] = result.priority_score
        metadata["priority_tier"] = result.priority_tier
        metadata["priority_reasons"] = result.reasons
        metadata["noise_reasons"] = result.noise_reasons
        metadata["cluster"] = {
            "source_count": len({event_source.source_id for event_source in event_sources}),
            "source_keys": sorted(
                {
                    event_source.source.key
                    for event_source in event_sources
                    if event_source.source is not None
                }
            ),
        }
        event.metadata_ = metadata
        session.commit()
        return {"status": "success", "event_id": event_id}
