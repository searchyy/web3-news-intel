from __future__ import annotations

import uuid

from app.db.session import SessionLocal
from app.scheduler.planner import due_sources, mark_source_queued
from app.workers.tasks_fetch import fetch_source


def enqueue_due_sources_once() -> int:
    with SessionLocal() as session:
        count = 0
        for source in due_sources(session):
            fetch_run = mark_source_queued(session, source, trace_id=str(uuid.uuid4()))
            if fetch_run is not None:
                fetch_source.delay(source.key, fetch_run.id)
                count += 1
        session.commit()
        return count
