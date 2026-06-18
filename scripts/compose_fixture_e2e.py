from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select

from app.adapters.rss import RSSAdapter
from app.core.config import SourceConfig
from app.db.models import Delivery, Event, EventSource, FetchRun, RawDocument, Source
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.db.session import SessionLocal
from app.pipeline.dedupe import DedupeService
from app.publishers.base import DeliveryManager, PublisherResult
from app.schemas.raw_document import RawDocumentPayload


class ComposeFixturePublisher:
    channel = "webhook"
    target = "compose-fixture://local"

    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, event: Event) -> PublisherResult:
        self.calls += 1
        return PublisherResult(ok=True, external_id=f"compose-{event.id}")


async def run() -> dict[str, int]:
    fixture_path = Path("tests/fixtures/rss_sec.xml")
    body = fixture_path.read_text(encoding="utf-8")
    body_hash = sha256(body.encode("utf-8")).hexdigest()

    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.key == "compose_fixture_rss"))
        if source is None:
            source = Source(
                key="compose_fixture_rss",
                name="Compose Fixture RSS",
                source_type="regulator_official",
                adapter="rss",
                url="https://93.184.216.34/compose-fixture.xml",
                canonical_url="https://93.184.216.34/compose-fixture.xml",
                category="security",
                language="en",
                trust_score=100,
                poll_seconds=300,
                timeout_seconds=5,
                max_response_bytes=65536,
                enabled=True,
                allow_private_networks=False,
                allow_localhost=False,
                config={"parser_version": "compose_fixture_rss_v1"},
            )
            session.add(source)
            session.flush()

        source_config = SourceConfig(
            key=source.key,
            name=source.name,
            source_type=source.source_type,
            adapter=source.adapter,
            url=source.url,
            canonical_url=source.canonical_url,
            category=source.category,
            language=source.language,
            trust_score=source.trust_score,
            poll_seconds=source.poll_seconds,
            timeout_seconds=source.timeout_seconds,
            max_response_bytes=source.max_response_bytes,
            enabled=source.enabled,
            allow_private_networks=source.allow_private_networks,
            allow_localhost=source.allow_localhost,
            config=source.config,
        )

        adapter = RSSAdapter()
        raw_repo = RawDocumentRepository(session)
        dedupe = DedupeService(session)
        created_event_ids: set[int] = set()
        for _ in range(2):
            fetch_run = FetchRun(source_id=source.id, status="running", trace_id="compose-e2e")
            session.add(fetch_run)
            session.flush()
            raw = RawDocumentPayload(
                source_key=source.key,
                url=source.url,
                canonical_url=source.canonical_url,
                content_type="application/rss+xml",
                status_code=200,
                body_hash=body_hash,
                body=body,
            )
            raw_document = raw_repo.upsert(source, raw, fetch_run_id=fetch_run.id)
            items = await adapter.parse(source_config, raw)
            for item in items:
                event = dedupe.upsert_event(item, source=source, raw_document=raw_document)
                created_event_ids.add(event.id)
            fetch_run.status = "success"
            fetch_run.item_count = len(items)
            session.commit()

        events = list(session.scalars(select(Event).where(Event.id.in_(created_event_ids))))
        if len(events) != 1:
            raise SystemExit(f"expected 1 event, found {len(events)}")
        event = events[0]
        if event.confirmation_count != 1:
            raise SystemExit(f"expected confirmation_count=1, found {event.confirmation_count}")

        publisher = ComposeFixturePublisher()
        manager = DeliveryManager(session)
        await manager.publish_once(event, publisher)
        await manager.publish_once(event, publisher)
        session.commit()
        if publisher.calls != 1:
            raise SystemExit(f"expected 1 publisher call, found {publisher.calls}")

        event_count = session.scalar(
            select(func.count(Event.id)).where(Event.id.in_(created_event_ids))
        )
        event_source_count = session.scalar(
            select(func.count(EventSource.id)).where(EventSource.event_id == event.id)
        )
        delivery_count = session.scalar(
            select(func.count(Delivery.id)).where(Delivery.event_id == event.id)
        )
        raw_count = session.scalar(
            select(func.count(RawDocument.id)).where(
                RawDocument.source_id == source.id, RawDocument.body_hash == body_hash
            )
        )
        result = {
            "events": int(event_count or 0),
            "event_sources": int(event_source_count or 0),
            "deliveries": int(delivery_count or 0),
            "raw_documents": int(raw_count or 0),
            "confirmation_count": event.confirmation_count,
        }
        if result != {
            "events": 1,
            "event_sources": 1,
            "deliveries": 1,
            "raw_documents": 1,
            "confirmation_count": 1,
        }:
            raise SystemExit(f"compose fixture E2E failed: {result}")
        return result


def main() -> None:
    print(json.dumps(asyncio.run(run()), sort_keys=True))


if __name__ == "__main__":
    main()
