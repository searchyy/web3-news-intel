from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import AccessDeniedError
from app.core.time import utc_now
from app.db.models import FetchRun, Source
from app.scheduler.planner import due_sources, queue_due_sources
from app.workers import tasks_fetch


def test_due_sources_skips_active_runs_and_open_circuits(db_session) -> None:
    due = _source("due")
    queued = _source("queued")
    circuit_open = _source("circuit_open", circuit_open_until=utc_now() + timedelta(minutes=5))
    db_session.add_all([due, queued, circuit_open])
    db_session.flush()
    db_session.add(FetchRun(source_id=queued.id, status="queued", trace_id="queued"))
    db_session.commit()

    assert [source.key for source in due_sources(db_session)] == ["due"]


def test_duplicate_scheduler_tick_does_not_enqueue_second_active_run(db_session) -> None:
    source = _source("single-active")
    db_session.add(source)
    db_session.commit()

    first = queue_due_sources(db_session, trace_id_factory=lambda: "trace-1")
    db_session.commit()
    second = queue_due_sources(db_session, trace_id_factory=lambda: "trace-2")
    db_session.commit()

    assert len(first) == 1
    assert second == []
    assert db_session.scalar(select(func.count()).select_from(FetchRun)) == 1
    assert db_session.scalar(select(FetchRun.status)) == "queued"
    assert db_session.scalar(select(FetchRun.queued_at)) is not None


def test_poll_sources_commits_queued_run_before_enqueue(monkeypatch, db_session) -> None:
    source = _source("commit-before-enqueue")
    db_session.add(source)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)
    seen: list[str] = []

    monkeypatch.setattr(tasks_fetch, "SessionLocal", session_local)
    monkeypatch.setattr(tasks_fetch, "_sync_sources_file", lambda session: None)

    def fake_delay(source_key: str, fetch_run_id: int) -> None:
        with session_local() as session:
            fetch_run = session.get(FetchRun, fetch_run_id)
            assert fetch_run is not None
            assert fetch_run.status == "queued"
            seen.append(source_key)

    monkeypatch.setattr(tasks_fetch.fetch_source, "delay", fake_delay)

    assert tasks_fetch.poll_sources.run() == {"queued": 1}
    assert seen == ["commit-before-enqueue"]


def test_poll_sources_marks_enqueue_failure_without_active_orphan(monkeypatch, db_session) -> None:
    source = _source("enqueue-failure")
    db_session.add(source)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)

    monkeypatch.setattr(tasks_fetch, "SessionLocal", session_local)
    monkeypatch.setattr(tasks_fetch, "_sync_sources_file", lambda session: None)

    def broken_delay(_source_key: str, _fetch_run_id: int) -> None:
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(tasks_fetch.fetch_source, "delay", broken_delay)

    assert tasks_fetch.poll_sources.run() == {"queued": 0}
    with session_local() as session:
        fetch_run = session.scalar(select(FetchRun).where(FetchRun.source_id == source.id))
        stored_source = session.get(Source, source.id)
        assert fetch_run is not None
        assert stored_source is not None
        assert fetch_run.status == "failed"
        assert fetch_run.error_code == "enqueue_failed"
        assert fetch_run.retry_after_until is not None
        assert stored_source.circuit_open_until == fetch_run.retry_after_until
        assert due_sources(session) == []


def test_active_fetch_claim_allows_only_one_running_run(monkeypatch, db_session) -> None:
    source = _source("claim-once")
    db_session.add(source)
    db_session.flush()
    first = FetchRun(source_id=source.id, status="queued", trace_id="first")
    db_session.add(first)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)
    monkeypatch.setattr(tasks_fetch, "SessionLocal", session_local)

    claim = tasks_fetch._claim_fetch_run(source.key, fetch_run_id=first.id)
    assert isinstance(claim, tasks_fetch._FetchClaim)
    duplicate = tasks_fetch._claim_fetch_run(source.key, fetch_run_id=None)

    with session_local() as session:
        stored = session.get(FetchRun, first.id)
        assert stored is not None
        assert stored.worker_started_at is not None
    assert duplicate == {"status": "skipped", "items": 0}
    assert stored.status == "running"


def test_stale_active_runs_expire_before_new_enqueue(db_session) -> None:
    old = utc_now() - timedelta(minutes=10)
    queued_source = _source("stale-queued")
    running_source = _source("stale-running", timeout_seconds=5)
    db_session.add_all([queued_source, running_source])
    db_session.flush()
    queued = FetchRun(
        source_id=queued_source.id,
        status="queued",
        trace_id="stale-queued",
        started_at=old,
        queued_at=old,
    )
    running = FetchRun(
        source_id=running_source.id,
        status="running",
        trace_id="stale-running",
        started_at=old,
        queued_at=old,
        worker_started_at=old,
    )
    db_session.add_all([queued, running])
    db_session.commit()

    trace_ids = iter(["new-trace-queued", "new-trace-running"])
    queued_runs = queue_due_sources(db_session, trace_id_factory=lambda: next(trace_ids))
    db_session.commit()

    assert {item.source_key for item in queued_runs} == {"stale-queued", "stale-running"}
    statuses = {
        run.trace_id: run.status
        for run in db_session.scalars(select(FetchRun).order_by(FetchRun.id))
    }
    assert statuses["stale-queued"] == "failed"
    assert statuses["stale-running"] == "failed"
    assert list(statuses.values()).count("queued") == 2


def test_fetch_failure_opens_circuit_and_success_clears_it(monkeypatch, db_session) -> None:
    source = _source("circuit")
    db_session.add(source)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)
    monkeypatch.setattr(tasks_fetch, "SessionLocal", session_local)

    claim = tasks_fetch._claim_fetch_run(source.key, fetch_run_id=None)
    assert isinstance(claim, tasks_fetch._FetchClaim)
    assert (
        tasks_fetch._finish_failed_fetch(
            claim,
            http_status=500,
            error_code="http_error",
            error_message="HTTP 500",
        )
        == {"status": "failed", "items": 0}
    )

    with session_local() as session:
        stored = session.scalar(select(Source).where(Source.key == source.key))
        assert stored is not None
        assert stored.health_status == "degraded"
        assert stored.consecutive_failures == 1
        assert stored.circuit_open_until is not None
        assert due_sources(session) == []
        tasks_fetch._record_source_success(
            stored,
            finished_at=utc_now(),
            http_status=200,
            parsed_count=3,
        )
        session.commit()

    with session_local() as session:
        stored = session.scalar(select(Source).where(Source.key == source.key))
        assert stored is not None
        assert stored.health_status == "healthy"
        assert stored.consecutive_failures == 0
        assert stored.circuit_open_until is None


def test_access_denied_disables_source_and_prevents_repeat_fetch(monkeypatch, db_session) -> None:
    source = _source("access-denied")
    db_session.add(source)
    db_session.commit()
    session_local = _sessionmaker_for(db_session)
    monkeypatch.setattr(tasks_fetch, "SessionLocal", session_local)

    claim = tasks_fetch._claim_fetch_run(source.key, fetch_run_id=None)
    assert isinstance(claim, tasks_fetch._FetchClaim)
    assert tasks_fetch._finish_access_denied(
        claim, AccessDeniedError(status_code=403)
    ) == {"status": "access_denied", "items": 0}
    repeat = tasks_fetch._claim_fetch_run(source.key, fetch_run_id=None)

    with session_local() as session:
        stored = session.scalar(select(Source).where(Source.key == source.key))
        assert stored is not None
        assert stored.enabled is False
        assert stored.health_status == "access_denied"
        assert stored.access_denied_at is not None
    assert repeat == {"status": "skipped", "items": 0}


def _sessionmaker_for(db_session):
    return sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _source(
    key: str,
    *,
    circuit_open_until=None,
    timeout_seconds: float = 5,
) -> Source:
    return Source(
        key=key,
        name=key,
        display_name_zh=None,
        source_group="test",
        source_type="rss",
        adapter="rss",
        url=f"https://example.com/{key}.xml",
        canonical_url=f"https://example.com/{key}.xml",
        category="security",
        language="en",
        official=True,
        trust_score=90,
        poll_seconds=60,
        timeout_seconds=timeout_seconds,
        max_response_bytes=1024,
        max_items_per_fetch=50,
        enabled=True,
        allow_private_networks=False,
        allow_localhost=False,
        health_status="healthy",
        live_canary_status="unknown",
        supported_categories=[],
        consecutive_failures=0,
        circuit_open_until=circuit_open_until,
        config={},
    )
