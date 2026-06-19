from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.adapters.registry import registry
from app.api.routes import events as event_routes
from app.core.config import SourceConfig, load_sources
from app.core.errors import AccessDeniedError, FetchError, InvalidContentTypeError
from app.db.base import Base
from app.db.models import Delivery, Event, EventSource, RawDocument
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.db.repositories.source_repo import SourceRepository
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter
from app.main import create_app
from app.pipeline.dedupe import DedupeService
from app.publishers.base import DeliveryManager, PublisherResult
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

REPORT_VERSION = "live_acceptance_v1"
PASS_STATUSES = {"PASS"}
LIVE_RESULT_VALUES = {
    "PASS",
    "DEGRADED",
    "ACCESS_DENIED",
    "EMPTY",
    "PARSER_BROKEN",
    "NETWORK_FAILED",
}


class LiveAcceptancePublisher:
    channel = "webhook"
    target = "live-acceptance://local"

    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, event: Event) -> PublisherResult:
        self.calls += 1
        return PublisherResult(ok=True, external_id=f"live-{event.id}")


async def run(
    path: str,
    *,
    reports_dir: Path,
    max_sources: int | None = None,
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    sources = load_sources(path).enabled_sources()
    if max_sources is not None:
        sources = sources[:max_sources]

    source_results: list[dict[str, Any]] = []
    first_run_payloads: dict[str, tuple[list[RawDocumentPayload], list[NormalizedItem]]] = {}
    for source in sources:
        result, raw_documents, items = await _fetch_and_parse(source)
        source_results.append(result)
        if result["result"] == "PASS":
            first_run_payloads[source.key] = (raw_documents, items)

    pipeline = await _run_pipeline(reports_dir, sources, first_run_payloads)
    verdicts = _verdicts(source_results, pipeline)
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _utc_now_iso(),
        "commit": _git_commit(),
        "sources_file": path,
        "tested_source_count": len(source_results),
        "source_results": source_results,
        "pipeline": pipeline,
        "verdicts": verdicts,
    }
    _write_reports(report, reports_dir)
    return report


async def _fetch_and_parse(
    source: SourceConfig,
) -> tuple[dict[str, Any], list[RawDocumentPayload], list[NormalizedItem]]:
    adapter = registry.get(source.adapter)
    result: dict[str, Any] = {
        "source_key": source.key,
        "adapter": source.adapter,
        "source_url": source.url,
        "http_status": None,
        "final_url": None,
        "content_type": None,
        "response_byte_count": 0,
        "response_sha256": None,
        "parsed_item_count": 0,
        "newest_published_at": None,
        "sample_title": None,
        "sample_original_url": None,
        "result": "NETWORK_FAILED",
        "error": None,
    }
    raw_documents: list[RawDocumentPayload] = []
    items: list[NormalizedItem] = []
    try:
        async with FetchClient(
            timeout_seconds=min(source.timeout_seconds, 15),
            max_response_bytes=min(source.max_response_bytes, 1024 * 1024),
            rate_limiter=HostRateLimiter(1),
            max_retries=1,
            allow_private_networks=source.allow_private_networks,
            allow_localhost=source.allow_localhost,
        ) as fetch_client:
            raw_documents = await adapter.fetch(source, fetch_client)
        for raw in raw_documents:
            result["http_status"] = raw.status_code
            result["final_url"] = raw.metadata.get("final_url") or raw.url
            result["content_type"] = raw.content_type
            result["response_byte_count"] += int(
                raw.metadata.get("response_bytes") or len((raw.body or "").encode("utf-8"))
            )
            result["response_sha256"] = raw.body_hash
            if _looks_like_access_challenge(raw.body or "", raw.content_type):
                result["result"] = "ACCESS_DENIED"
                result["error"] = "response resembles an access challenge or login page"
                return result, raw_documents, []
            items.extend(await adapter.parse(source, raw))
        result["parsed_item_count"] = len(items)
        if not raw_documents:
            result["result"] = "EMPTY"
            result["error"] = "no raw documents returned"
        elif not items:
            result["result"] = "EMPTY"
            result["error"] = "no parsed items returned"
        elif _items_have_rejected_urls(items):
            result["result"] = "PARSER_BROKEN"
            result["error"] = "parsed item URL was empty, fixture-like, or example.com"
        else:
            newest = _newest(items)
            result["newest_published_at"] = newest.isoformat() if newest else None
            result["sample_title"] = items[0].title
            result["sample_original_url"] = items[0].url
            result["result"] = "PASS"
    except AccessDeniedError as exc:
        result["http_status"] = exc.status_code
        result["result"] = "ACCESS_DENIED"
        result["error"] = str(exc)
    except InvalidContentTypeError as exc:
        result["result"] = "PARSER_BROKEN"
        result["error"] = str(exc)
    except FetchError as exc:
        result["result"] = "NETWORK_FAILED"
        result["error"] = str(exc)
    except Exception as exc:
        result["result"] = "PARSER_BROKEN"
        result["error"] = f"{type(exc).__name__}: {exc}"
    if result["result"] not in LIVE_RESULT_VALUES:
        result["result"] = "DEGRADED"
    return result, raw_documents, items


async def _run_pipeline(
    reports_dir: Path,
    sources: list[SourceConfig],
    first_run_payloads: dict[str, tuple[list[RawDocumentPayload], list[NormalizedItem]]],
) -> dict[str, Any]:
    database_path = reports_dir / "live_acceptance.sqlite"
    if database_path.exists():
        database_path.unlink()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    source_by_key = {source.key: source for source in sources}
    publisher = LiveAcceptancePublisher()
    event_ids: set[int] = set()
    raw_document_ids: set[int] = set()
    event_source_ids: set[int] = set()
    first_confirmation_counts: dict[str, int] = {}
    first_event_keys: set[str] = set()
    first_event_count = 0
    second_run_new_event_keys: set[str] = set()

    with SessionLocal() as session:
        source_repo = SourceRepository(session)
        source_models = {
            source.key: source_repo.upsert_from_config(source) for source in sources
        }
        session.flush()

        for source_key, (raw_documents, _items) in first_run_payloads.items():
            source_model = source_models[source_key]
            raw_repo = RawDocumentRepository(session)
            dedupe = DedupeService(session)
            for raw in raw_documents:
                raw_document = raw_repo.upsert(source_model, raw)
                raw_document_ids.add(raw_document.id)
                for item in await registry.get(source_model.adapter).parse(
                    source_by_key[source_key], raw
                ):
                    event = dedupe.upsert_event(
                        item,
                        source=source_model,
                        raw_document=raw_document,
                    )
                    event_ids.add(event.id)
                    first_event_keys.add(event.event_key)
            session.commit()

        first_event_count = _count(session, Event.id)
        first_confirmation_counts = {
            event.event_key: event.confirmation_count
            for event in session.scalars(select(Event).where(Event.event_key.in_(first_event_keys)))
        }
        await _publish_all(session, publisher)
        session.commit()

        for source_key in sorted(first_run_payloads):
            source_config = source_by_key[source_key]
            source_model = source_models[source_key]
            result, raw_documents, _items = await _fetch_and_parse(source_config)
            if result["result"] != "PASS":
                continue
            raw_repo = RawDocumentRepository(session)
            dedupe = DedupeService(session)
            for raw in raw_documents:
                raw_document = raw_repo.upsert(source_model, raw)
                raw_document_ids.add(raw_document.id)
                for item in await registry.get(source_config.adapter).parse(source_config, raw):
                    event_key = dedupe.build_event_key(item)
                    event = dedupe.upsert_event(
                        item,
                        source=source_model,
                        raw_document=raw_document,
                    )
                    event_ids.add(event.id)
                    if event_key not in first_event_keys:
                        second_run_new_event_keys.add(event_key)
            session.commit()
        await _publish_all(session, publisher)
        session.commit()

        for event_source_id in session.scalars(select(EventSource.id)):
            event_source_ids.add(event_source_id)
        second_confirmation_counts = {
            event.event_key: event.confirmation_count
            for event in session.scalars(select(Event).where(Event.event_key.in_(first_event_keys)))
        }
        confirmation_count_increased = [
            key
            for key, first_count in first_confirmation_counts.items()
            if second_confirmation_counts.get(key, first_count) > first_count
        ]
        duplicate_event_keys = _duplicate_values(
            session, select(Event.event_key, func.count(Event.id)).group_by(Event.event_key)
        )
        duplicate_delivery_keys = _duplicate_values(
            session,
            select(Delivery.idempotency_key, func.count(Delivery.id)).group_by(
                Delivery.idempotency_key
            ),
        )
        duplicate_event_source_keys = _duplicate_values(
            session,
            select(
                EventSource.event_id,
                func.count(EventSource.id),
            ).group_by(EventSource.event_id, EventSource.source_id, EventSource.url),
        )
        api_result = _api_result(SessionLocal)
        event_details = _event_details(session, event_ids)
        result = {
            "database_path": str(database_path),
            "raw_document_ids": sorted(raw_document_ids),
            "event_ids": sorted(event_ids),
            "event_source_ids": sorted(event_source_ids),
            "delivery_ids": list(session.scalars(select(Delivery.id).order_by(Delivery.id))),
            "raw_document_count": _count(session, RawDocument.id),
            "normalized_item_event_count_after_first_run": first_event_count,
            "event_count": _count(session, Event.id),
            "event_source_count": _count(session, EventSource.id),
            "delivery_count": _count(session, Delivery.id),
            "duplicate_events_created": len(duplicate_event_keys),
            "duplicate_deliveries_created": len(duplicate_delivery_keys),
            "duplicate_event_sources_created": len(duplicate_event_source_keys),
            "confirmation_count_increased_on_same_source_rerun": confirmation_count_increased,
            "new_source_publications_between_runs": sorted(second_run_new_event_keys),
            "publisher_calls": publisher.calls,
            "api": api_result,
            "events": event_details,
        }
    engine.dispose()
    return result


async def _publish_all(session, publisher: LiveAcceptancePublisher) -> None:
    manager = DeliveryManager(session)
    for event in session.scalars(select(Event).order_by(Event.id)):
        await manager.publish_once(event, publisher)
        await manager.publish_once(event, publisher)


def _api_result(SessionLocal) -> dict[str, Any]:
    app = create_app()

    def override_session():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[event_routes.get_session] = override_session
    with TestClient(app) as client:
        response = client.get("/events", params={"limit": 50})
        payload = response.json()
    return {
        "status_code": response.status_code,
        "event_count": len(payload) if isinstance(payload, list) else None,
        "sample": [_sanitize_event(event) for event in payload[:5]]
        if isinstance(payload, list)
        else payload,
    }


def _event_details(session, event_ids: Iterable[int]) -> list[dict[str, Any]]:
    events = list(session.scalars(select(Event).where(Event.id.in_(event_ids)).order_by(Event.id)))
    return [_sanitize_event_model(event) for event in events]


def _sanitize_event_model(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_key": event.event_key,
        "title": event.title,
        "category": event.category,
        "status": event.status,
        "severity": event.severity,
        "primary_url": event.primary_url,
        "published_at": event.published_at.isoformat() if event.published_at else None,
        "confirmation_count": event.confirmation_count,
        "symbols": event.symbols,
        "source_count": len(event.sources),
    }


def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "event_key": event.get("event_key"),
        "title": event.get("title"),
        "category": event.get("category"),
        "status": event.get("status"),
        "severity": event.get("severity"),
        "primary_url": event.get("primary_url"),
        "published_at": event.get("published_at"),
        "confirmation_count": event.get("confirmation_count"),
        "symbols": event.get("symbols") or [],
    }


def _count(session, column) -> int:
    return int(session.scalar(select(func.count(column))) or 0)


def _duplicate_values(session, statement) -> list[Any]:
    duplicates: list[Any] = []
    for row in session.execute(statement):
        if row[-1] > 1:
            duplicates.append(row[0])
    return duplicates


def _verdicts(source_results: list[dict[str, Any]], pipeline: dict[str, Any]) -> dict[str, str]:
    successful = [result for result in source_results if result["result"] == "PASS"]
    official_success = any(
        result["source_key"] in {"sec_press", "cftc_press", "ethereum_blog"}
        for result in successful
    )
    live_fetch = "PASS" if len(successful) >= 2 and official_success else "DEGRADED"
    live_pipeline = (
        "PASS"
        if pipeline["raw_document_count"] > 0
        and pipeline["event_count"] > 0
        and pipeline["event_source_count"] > 0
        else "DEGRADED"
    )
    dedupe = (
        "PASS"
        if pipeline["duplicate_events_created"] == 0
        and pipeline["duplicate_deliveries_created"] == 0
        and pipeline["duplicate_event_sources_created"] == 0
        and not pipeline["confirmation_count_increased_on_same_source_rerun"]
        else "DEGRADED"
    )
    api = (
        "PASS"
        if pipeline["api"]["status_code"] == 200 and (pipeline["api"]["event_count"] or 0) > 0
        else "DEGRADED"
    )
    publisher = (
        "PASS"
        if pipeline["delivery_count"] > 0 and pipeline["duplicate_deliveries_created"] == 0
        else "DEGRADED"
    )
    overall = (
        "PASS"
        if all(
            verdict in PASS_STATUSES
            for verdict in (live_fetch, live_pipeline, dedupe, api, publisher)
        )
        else "DEGRADED"
    )
    return {
        "LIVE_FETCH": live_fetch,
        "LIVE_PIPELINE": live_pipeline,
        "DEDUPE": dedupe,
        "API": api,
        "PUBLISHER": publisher,
        "OVERALL_LIVE_ACCEPTANCE": overall,
    }


def _write_reports(report: dict[str, Any], reports_dir: Path) -> None:
    json_path = reports_dir / "live_acceptance.json"
    markdown_path = reports_dir / "live_acceptance.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Live Source Acceptance",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Commit: `{report['commit']}`",
        f"- Sources tested: `{report['tested_source_count']}`",
        "",
        "## Verdicts",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    for gate, result in report["verdicts"].items():
        lines.append(f"| {gate} | {result} |")
    lines.extend(
        [
            "",
            "## Source Results",
            "",
            "| Source | Adapter | Status | Items | Newest | Sample Title | Sample URL |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for result in report["source_results"]:
        title = _md_escape(result.get("sample_title") or "")
        url = _md_escape(result.get("sample_original_url") or "")
        lines.append(
            "| "
            f"{result['source_key']} | {result['adapter']} | {result['result']} | "
            f"{result['parsed_item_count']} | {result.get('newest_published_at') or ''} | "
            f"{title} | {url} |"
        )
    pipeline = report["pipeline"]
    lines.extend(
        [
            "",
            "## Pipeline",
            "",
            f"- Raw documents: `{pipeline['raw_document_count']}`",
            f"- Events: `{pipeline['event_count']}`",
            f"- Event sources: `{pipeline['event_source_count']}`",
            f"- Deliveries: `{pipeline['delivery_count']}`",
            f"- Duplicate events created: `{pipeline['duplicate_events_created']}`",
            f"- Duplicate deliveries created: `{pipeline['duplicate_deliveries_created']}`",
            f"- Duplicate event sources created: `{pipeline['duplicate_event_sources_created']}`",
            f"- Confirmation increases on same-source rerun: "
            f"`{len(pipeline['confirmation_count_increased_on_same_source_rerun'])}`",
            f"- API status: `{pipeline['api']['status_code']}`",
            f"- API event count: `{pipeline['api']['event_count']}`",
            "",
            "## Event Sample",
            "",
        ]
    )
    for event in pipeline["events"][:10]:
        lines.append(
            f"- `{event['id']}` {event['title']} "
            f"({event['category']}, {event['status']}, confirmations={event['confirmation_count']})"
        )
    lines.append("")
    return "\n".join(lines)


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _newest(items: list[NormalizedItem]) -> datetime | None:
    published = [item.published_at for item in items if item.published_at is not None]
    if not published:
        return None
    return max(value.astimezone(UTC) for value in published)


def _items_have_rejected_urls(items: list[NormalizedItem]) -> bool:
    for item in items:
        if not item.url or "example.com" in item.url or "tests/fixtures" in item.url:
            return True
    return False


def _looks_like_access_challenge(body: str, content_type: str | None) -> bool:
    lowered = body[:5000].lower()
    html_like = "html" in (content_type or "").lower() or "<html" in lowered
    if not html_like:
        return False
    markers = (
        "captcha",
        "cloudflare",
        "attention required",
        "enable javascript",
        "sign in",
        "login page",
    )
    return any(marker in lowered for marker in markers)


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources-file", default="sources.yaml")
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--reports-dir", default="reports")
    args = parser.parse_args()
    report = asyncio.run(
        run(
            args.sources_file,
            reports_dir=Path(args.reports_dir),
            max_sources=args.max_sources,
        )
    )
    print(json.dumps(report["verdicts"], indent=2, sort_keys=True))
    if report["verdicts"]["OVERALL_LIVE_ACCEPTANCE"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
