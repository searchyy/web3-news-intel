from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.db.models import FetchRun, Source


def due_sources(session: Session) -> list[Source]:
    latest_runs = (
        select(FetchRun.source_id, func.max(FetchRun.started_at).label("last_started_at"))
        .group_by(FetchRun.source_id)
        .subquery()
    )
    candidates = session.scalars(
        select(Source)
        .outerjoin(latest_runs, latest_runs.c.source_id == Source.id)
        .where(Source.enabled.is_(True))
    )
    now = utc_now()
    due: list[Source] = []
    for source in candidates:
        last_started_at = session.scalar(
            select(func.max(FetchRun.started_at)).where(FetchRun.source_id == source.id)
        )
        last_started_at = ensure_utc(last_started_at)
        if last_started_at is None or last_started_at <= now - timedelta(
            seconds=source.poll_seconds
        ):
            due.append(source)
    return due


def mark_source_queued(session: Session, source: Source, *, trace_id: str) -> FetchRun | None:
    now = utc_now()
    last_started_at = session.scalar(
        select(func.max(FetchRun.started_at)).where(FetchRun.source_id == source.id)
    )
    last_started_at = ensure_utc(last_started_at)
    if last_started_at is not None and last_started_at > now - timedelta(
        seconds=source.poll_seconds
    ):
        return None
    fetch_run = FetchRun(
        source_id=source.id,
        status="queued",
        trace_id=trace_id,
        started_at=now,
    )
    session.add(fetch_run)
    session.flush()
    return fetch_run
