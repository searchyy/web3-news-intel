from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.db.models import FetchRun, Source

ACTIVE_FETCH_STATUSES = ("queued", "running")
STALE_QUEUED_AFTER_SECONDS = 300
MIN_STALE_RUNNING_AFTER_SECONDS = 300


@dataclass(frozen=True, slots=True)
class QueuedFetchRun:
    source_key: str
    fetch_run_id: int


def due_sources(session: Session) -> list[Source]:
    now = utc_now()
    latest_runs = (
        select(FetchRun.source_id, func.max(FetchRun.started_at).label("last_started_at"))
        .group_by(FetchRun.source_id)
        .subquery()
    )
    candidates = session.execute(
        select(Source)
        .add_columns(latest_runs.c.last_started_at)
        .outerjoin(latest_runs, latest_runs.c.source_id == Source.id)
        .where(Source.enabled.is_(True))
        .where(
            or_(
                Source.circuit_open_until.is_(None),
                Source.circuit_open_until <= now,
            )
        )
    )
    due: list[Source] = []
    for source, last_started_at in candidates:
        if expire_stale_fetch_runs(session, source, now=now):
            session.flush()
        if _has_active_fetch_run(session, source.id):
            continue
        last_started_at = ensure_utc(last_started_at)
        if last_started_at is None or last_started_at <= now - timedelta(
            seconds=source.poll_seconds
        ):
            due.append(source)
    return due


def begin_polling_write_lock(session: Session) -> bool:
    """Serialize SQLite polling decisions; PostgreSQL uses row locks below."""
    bind = session.get_bind()
    if bind.dialect.name != "sqlite" or session.in_transaction():
        return True
    try:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError:
        session.rollback()
        return False
    return True


def queue_due_sources(session: Session, *, trace_id_factory) -> list[QueuedFetchRun]:
    if not begin_polling_write_lock(session):
        return []
    queued: list[QueuedFetchRun] = []
    for source in due_sources(session):
        fetch_run = mark_source_queued(session, source, trace_id=trace_id_factory())
        if fetch_run is not None:
            queued.append(QueuedFetchRun(source.key, fetch_run.id))
    return queued


def mark_source_queued(
    session: Session, source: Source, *, trace_id: str, force: bool = False
) -> FetchRun | None:
    now = utc_now()
    source = _lock_source_for_polling(session, source.id)
    if source is None or not source.enabled:
        return None
    circuit_open_until = ensure_utc(source.circuit_open_until)
    if circuit_open_until is not None and circuit_open_until > now and not force:
        return None
    if expire_stale_fetch_runs(session, source, now=now):
        session.flush()
    if _has_active_fetch_run(session, source.id):
        return None
    if not force:
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
        queued_at=now,
        started_at=now,
    )
    session.add(fetch_run)
    session.flush()
    return fetch_run


def _lock_source_for_polling(session: Session, source_id: int) -> Source | None:
    stmt = select(Source).where(Source.id == source_id)
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return session.scalar(stmt)


def _has_active_fetch_run(session: Session, source_id: int) -> bool:
    return (
        session.scalar(
            select(FetchRun.id)
            .where(
                FetchRun.source_id == source_id,
                FetchRun.status.in_(ACTIVE_FETCH_STATUSES),
            )
            .limit(1)
        )
        is not None
    )


def expire_stale_fetch_runs(session: Session, source: Source, *, now=None) -> int:
    now = now or utc_now()
    running_after_seconds = max(
        MIN_STALE_RUNNING_AFTER_SECONDS,
        int(float(source.timeout_seconds or 15) * 4),
    )
    expired = 0
    for fetch_run in session.scalars(
        select(FetchRun)
        .where(
            FetchRun.source_id == source.id,
            FetchRun.status.in_(ACTIVE_FETCH_STATUSES),
        )
        .order_by(FetchRun.started_at)
    ):
        if fetch_run.status == "queued":
            queued_at = ensure_utc(fetch_run.queued_at or fetch_run.started_at)
            if queued_at is not None and queued_at <= now - timedelta(
                seconds=STALE_QUEUED_AFTER_SECONDS
            ):
                _expire_fetch_run(fetch_run, now=now, error_code="stale_queued")
                expired += 1
        elif fetch_run.status == "running":
            worker_started_at = ensure_utc(fetch_run.worker_started_at or fetch_run.started_at)
            if worker_started_at is not None and worker_started_at <= now - timedelta(
                seconds=running_after_seconds
            ):
                _expire_fetch_run(fetch_run, now=now, error_code="stale_running")
                expired += 1
    return expired


def _expire_fetch_run(fetch_run: FetchRun, *, now, error_code: str) -> None:
    fetch_run.status = "failed"
    fetch_run.finished_at = now
    fetch_run.error_code = error_code
    fetch_run.error_message = error_code
