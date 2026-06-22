from __future__ import annotations

import uuid

from app.db.session import SessionLocal
from app.scheduler.planner import queue_due_sources
from app.workers.tasks_fetch import enqueue_fetch_run


def enqueue_due_sources_once() -> int:
    with SessionLocal() as session:
        queued = queue_due_sources(session, trace_id_factory=lambda: str(uuid.uuid4()))
        session.commit()
    return sum(
        1 for fetch_run in queued if enqueue_fetch_run(fetch_run.source_key, fetch_run.fetch_run_id)
    )
