from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import SourceConfig
from app.db.models import Event, EventSource, FetchRun, Source
from app.db.repositories.delivery_repo import DeliveryRepository
from app.db.repositories.event_repo import EventRepository
from app.db.repositories.source_repo import SourceRepository
from app.pipeline.dedupe import DedupeService
from app.schemas.normalized_item import NormalizedItem

pytestmark = pytest.mark.postgres


def test_jsonb_text_array_and_timezone_columns(postgres_session) -> None:
    event = Event(
        event_key="pg:types",
        title="PostgreSQL Types",
        category="listing",
        status="confirmed",
        severity="high",
        published_at=datetime(2026, 6, 18, 1, 0, tzinfo=UTC),
        trust_score=95,
        confirmation_count=1,
        symbols=["ABC", "ETH"],
        chains=["ethereum"],
        entities=["Example"],
        metadata_={"nested": {"ok": True}},
    )
    postgres_session.add(event)
    postgres_session.flush()
    loaded = postgres_session.scalar(select(Event).where(Event.event_key == "pg:types"))
    assert loaded.metadata_["nested"]["ok"] is True
    assert loaded.symbols == ["ABC", "ETH"]
    assert loaded.published_at.tzinfo is not None
    assert loaded.published_at.utcoffset().total_seconds() == 0


def test_constraints_indexes_foreign_keys_and_migration_version(postgres_session) -> None:
    bind = postgres_session.get_bind()
    inspector = inspect(bind)
    version = postgres_session.execute(text("select version_num from alembic_version")).scalar_one()
    assert version == "0007_fetch_run_queue_obs"

    extensions = {
        row[0]
        for row in postgres_session.execute(
            text("select extname from pg_extension where extname = 'pg_trgm'")
        )
    }
    assert "pg_trgm" in extensions

    event_indexes = {index["name"] for index in inspector.get_indexes("events")}
    assert "ix_events_event_key" in event_indexes
    assert "ix_events_published_at" in event_indexes
    assert "ix_events_category" in event_indexes
    assert "ix_events_first_seen_at" in event_indexes
    assert "ix_events_status_severity_first_seen" in event_indexes
    assert "ix_events_category_first_seen" in event_indexes

    postgres_indexes = {
        row[0]
        for row in postgres_session.execute(
            text(
                """
                select indexname
                from pg_indexes
                where schemaname = current_schema()
                  and tablename = 'events'
                """
            )
        )
    }
    assert "ix_events_title_trgm" in postgres_indexes
    assert "ix_events_summary_trgm" in postgres_indexes
    assert "ix_events_symbols_gin" in postgres_indexes
    assert "ix_events_chains_gin" in postgres_indexes
    assert "ix_events_entities_gin" in postgres_indexes

    fetch_run_indexes = {index["name"] for index in inspector.get_indexes("fetch_runs")}
    assert "ix_fetch_runs_source_status_started" in fetch_run_indexes
    assert "ix_fetch_runs_task_id" in fetch_run_indexes
    assert "uq_fetch_runs_active_source" in fetch_run_indexes

    event_source_fks = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys("event_sources")
    }
    assert ("event_id",) in event_source_fks
    assert ("source_id",) in event_source_fks
    assert ("raw_document_id",) in event_source_fks

    with pytest.raises(IntegrityError):
        with postgres_session.begin_nested():
            postgres_session.add(FetchRun(source_id=999999, status="queued", trace_id="missing-fk"))
            postgres_session.flush()


def test_source_upsert(postgres_session) -> None:
    repo = SourceRepository(postgres_session)
    config = _source_config("pg_source", trust_score=70)
    repo.upsert_from_config(config)
    repo.upsert_from_config(config.model_copy(update={"trust_score": 90}))
    postgres_session.flush()
    source = repo.get_by_key("pg_source")
    assert source is not None
    assert source.trust_score == 90


def test_event_upsert_event_source_uniqueness_and_delivery_idempotency(postgres_session) -> None:
    source = _source_model("pg_dedupe")
    postgres_session.add(source)
    postgres_session.flush()
    item = _item("https://example.com/a")
    event = DedupeService(postgres_session).upsert_event(item, source=source)
    DedupeService(postgres_session).upsert_event(item, source=source)
    postgres_session.flush()
    assert len(event.sources) == 1
    with pytest.raises(IntegrityError):
        with postgres_session.begin_nested():
            postgres_session.add(
                EventSource(
                    event_id=event.id,
                    source_id=source.id,
                    url=item.url,
                    source_score=90,
                )
            )
            postgres_session.flush()

    delivery_repo = DeliveryRepository(postgres_session)
    first = delivery_repo.ensure_pending(
        event, channel="webhook", target="https://example.com", idempotency_key="same"
    )
    second = delivery_repo.ensure_pending(
        event, channel="webhook", target="https://example.com", idempotency_key="same"
    )
    assert first.id == second.id


def test_concurrent_event_creation_is_idempotent(monkeypatch) -> None:
    import os

    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL must point to PostgreSQL")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid4().hex
    source_key = f"pg_concurrent_{suffix}"
    item_url = f"https://example.com/concurrent/{suffix}"
    with SessionLocal() as session:
        source = _source_model(source_key)
        session.add(source)
        session.commit()
        source_id = source.id

    def create_once() -> int:
        with SessionLocal() as session:
            source = session.get(Source, source_id)
            event = DedupeService(session).upsert_event(_item(item_url), source=source)
            session.commit()
            return event.id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: create_once(), range(2)))
        assert len(set(ids)) == 1
    finally:
        with SessionLocal() as session:
            event_ids = list(
                session.scalars(select(Event.id).where(Event.primary_url == item_url))
            )
            if event_ids:
                session.execute(delete(EventSource).where(EventSource.event_id.in_(event_ids)))
                session.execute(delete(Event).where(Event.id.in_(event_ids)))
            session.execute(delete(Source).where(Source.key == source_key))
            session.commit()
        engine.dispose()


def test_transaction_rollback(postgres_session) -> None:
    nested = postgres_session.begin_nested()
    postgres_session.add(_source_model("pg_rollback"))
    postgres_session.flush()
    nested.rollback()
    assert SourceRepository(postgres_session).get_by_key("pg_rollback") is None


def test_event_filtering(postgres_session) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)
    events = [
        Event(
            event_key="pg:filter:1",
            title="ABC listing",
            category="pg_filter_listing",
            status="pg_filter_confirmed",
            severity="pg_filter_high",
            published_at=now,
            trust_score=95,
            confirmation_count=1,
            symbols=["ABC"],
            chains=[],
            entities=[],
            metadata_={},
        ),
        Event(
            event_key="pg:filter:2",
            title="DEF exploit",
            category="pg_filter_exploit",
            status="pg_filter_needs_review",
            severity="pg_filter_critical",
            published_at=now - timedelta(days=2),
            trust_score=75,
            confirmation_count=1,
            symbols=["DEF"],
            chains=[],
            entities=[],
            metadata_={},
        ),
    ]
    postgres_session.add_all(events)
    postgres_session.flush()
    repo = EventRepository(postgres_session)
    assert [event.event_key for event in repo.list(category="pg_filter_listing")] == [
        "pg:filter:1"
    ]
    assert [event.event_key for event in repo.list(status="pg_filter_needs_review")] == [
        "pg:filter:2"
    ]
    assert [event.event_key for event in repo.list(severity="pg_filter_critical")] == [
        "pg:filter:2"
    ]
    assert [event.event_key for event in repo.list(symbol="ABC")] == ["pg:filter:1"]
    assert [event.event_key for event in repo.list(published_since=now - timedelta(days=1))] == [
        "pg:filter:1"
    ]


def _source_config(key: str, *, trust_score: int = 90) -> SourceConfig:
    return SourceConfig(
        key=key,
        name=key,
        source_type="tier1_media",
        adapter="rss",
        url=f"https://example.com/{key}.xml",
        canonical_url=f"https://example.com/{key}.xml",
        category="media",
        language="en",
        trust_score=trust_score,
        poll_seconds=300,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        config={"parser_version": "pg_test_v1"},
    )


def _source_model(key: str) -> Source:
    config = _source_config(key)
    return Source(**config.model_dump())


def _item(url: str) -> NormalizedItem:
    return NormalizedItem(
        title="Exchange Will List Example Token (ABC)",
        url=url,
        published_at=datetime(2026, 6, 18, tzinfo=UTC),
        source_key="pg",
        source_type="exchange_official",
        category="listing",
    )
