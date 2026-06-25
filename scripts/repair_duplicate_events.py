from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.db.models import Delivery, Event, EventAIInsight, EventSource
from app.db.session import SessionLocal, engine
from app.pipeline.dedupe import DedupeService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge duplicate events with the same primary_url."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; otherwise only prints a dry run",
    )
    parser.add_argument(
        "--backup-dir",
        default=".runtime/backups",
        help="backup directory for sqlite database",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.apply:
        _backup_sqlite(Path(args.backup_dir))

    with SessionLocal() as session:
        groups = _duplicate_url_groups(session)
        print(f"duplicate_url_groups={len(groups)}")
        merged_events = 0
        for url, _count in groups:
            events = list(
                session.scalars(
                    select(Event)
                    .options(
                        selectinload(Event.sources),
                        selectinload(Event.ai_insights),
                        selectinload(Event.deliveries),
                    )
                    .where(Event.primary_url == url)
                    .order_by(Event.first_seen_at.asc(), Event.id.asc())
                )
            )
            if len(events) < 2:
                continue
            keeper = events[0]
            print(f"url={url} keep={keeper.id} merge={[event.id for event in events[1:]]}")
            if not args.apply:
                merged_events += len(events) - 1
                continue
            for duplicate in events[1:]:
                _merge_event(session, keeper, duplicate)
                merged_events += 1
            DedupeService(session)._refresh_score(keeper)
        if args.apply:
            session.commit()
        print(f"merged_events={merged_events} applied={args.apply}")
    return 0


def _duplicate_url_groups(session):
    return list(
        session.execute(
            select(Event.primary_url, func.count(Event.id))
            .where(Event.primary_url.is_not(None), Event.primary_url != "")
            .group_by(Event.primary_url)
            .having(func.count(Event.id) > 1)
            .order_by(func.count(Event.id).desc(), Event.primary_url.asc())
        )
    )


def _merge_event(session, keeper: Event, duplicate: Event) -> None:
    keeper.first_seen_at = min(_dt(keeper.first_seen_at), _dt(duplicate.first_seen_at))
    keeper.last_seen_at = max(_dt(keeper.last_seen_at), _dt(duplicate.last_seen_at))
    keeper.published_at = _earliest(keeper.published_at, duplicate.published_at)
    keeper.trust_score = max(keeper.trust_score or 0, duplicate.trust_score or 0)
    keeper.confirmation_count = max(
        keeper.confirmation_count or 1,
        duplicate.confirmation_count or 1,
    )
    keeper.symbols = sorted(set(keeper.symbols or []) | set(duplicate.symbols or []))
    keeper.chains = sorted(set(keeper.chains or []) | set(duplicate.chains or []))
    keeper.entities = sorted(set(keeper.entities or []) | set(duplicate.entities or []))
    if not keeper.summary and duplicate.summary:
        keeper.summary = duplicate.summary
    metadata = dict(keeper.metadata_ or {})
    merged_ids = set(int(item) for item in metadata.get("merged_event_ids") or [])
    merged_ids.add(int(duplicate.id))
    metadata["merged_event_ids"] = sorted(merged_ids)
    keeper.metadata_ = metadata

    for event_source in list(duplicate.sources):
        existing = session.scalar(
            select(EventSource).where(
                EventSource.event_id == keeper.id,
                EventSource.source_id == event_source.source_id,
                EventSource.url == event_source.url,
            )
        )
        if existing is not None:
            session.delete(event_source)
        else:
            event_source.event_id = keeper.id
            event_source.event = keeper

    for insight in list(duplicate.ai_insights):
        existing = session.scalar(
            select(EventAIInsight).where(
                EventAIInsight.event_id == keeper.id,
                EventAIInsight.provider == insight.provider,
                EventAIInsight.model == insight.model,
                EventAIInsight.prompt_version == insight.prompt_version,
                EventAIInsight.input_hash == insight.input_hash,
            )
        )
        if existing is not None:
            if insight.status == "success" and existing.status != "success":
                session.delete(existing)
                insight.event_id = keeper.id
                insight.event = keeper
            else:
                session.delete(insight)
        else:
            insight.event_id = keeper.id
            insight.event = keeper

    for delivery in list(duplicate.deliveries):
        existing = session.scalar(
            select(Delivery).where(
                Delivery.destination_id == delivery.destination_id,
                Delivery.event_id == keeper.id,
                Delivery.delivery_variant == delivery.delivery_variant,
            )
        )
        if existing is not None:
            session.delete(delivery)
        else:
            delivery.event_id = keeper.id
            delivery.event = keeper

    duplicate_id = duplicate.id
    session.flush()
    session.expunge(duplicate)
    session.execute(delete(Event).where(Event.id == duplicate_id))
    session.flush()


def _dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _earliest(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(_dt(left), _dt(right))


def _backup_sqlite(backup_dir: Path) -> None:
    if engine.url.get_backend_name() != "sqlite":
        print("backup=skipped non-sqlite")
        return
    source = engine.url.database
    if not source or source == ":memory:":
        print("backup=skipped memory sqlite")
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{Path(source).stem}_before_duplicate_merge_{timestamp}.sqlite3"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    print(f"backup={target}")


if __name__ == "__main__":
    raise SystemExit(main())
