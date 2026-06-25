from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.core.time import ensure_utc, utc_now
from app.db.models import (
    Delivery,
    Event,
    EventSource,
    NotificationDestination,
    ReportSchedule,
    SavedSearch,
)
from app.db.repositories.delivery_repo import DeliveryRepository
from app.integrations.feishu.client import FeishuClient
from app.integrations.feishu.models import FeishuSendResult
from app.integrations.feishu.report_cards import ReportPreview, build_report_preview
from app.pipeline.scoring import event_priority_score
from app.publishers.feishu import _sanitize_error

SEVERITY_ORDER = {"critical": 4, "high": 3, "normal": 2, "low": 1}
REPORT_DEFAULT_PRIORITY = {
    "immediate": 85,
    "digest_15m": 70,
    "digest_30m": 70,
    "hourly": 70,
    "daily_morning": 55,
    "daily_evening": 55,
    "custom": 55,
}


@dataclass(slots=True)
class ReportSendOutcome:
    preview: ReportPreview
    delivery: Delivery | None
    dry_run: bool
    status: str
    message: str | None = None


class FeishuReportService:
    def __init__(
        self,
        session: Session,
        *,
        client: FeishuClient | None = None,
        encryptor: FieldEncryptor | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.encryptor = encryptor

    def preview(
        self,
        schedule: ReportSchedule,
        *,
        now: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ReportPreview:
        end = ensure_utc(window_end or now or utc_now()) or utc_now()
        start = (
            ensure_utc(window_start)
            if window_start
            else self.default_window_start(schedule, end)
        )
        events = self.events_for_window(schedule, window_start=start, window_end=end)
        return build_report_preview(
            schedule,
            window_start=start,
            window_end=end,
            events=events,
            total_count=len(events),
        )

    async def send_report_for_window(
        self,
        schedule: ReportSchedule,
        *,
        window_start: datetime,
        window_end: datetime,
        test_send: bool = False,
    ) -> ReportSendOutcome:
        preview = self.preview(schedule, window_start=window_start, window_end=window_end)
        if not preview.events and not test_send:
            _mark_schedule_run(
                schedule,
                window_start,
                window_end,
                status_time=utc_now(),
                result="empty",
                error=None,
            )
            return ReportSendOutcome(preview=preview, delivery=None, dry_run=True, status="empty")
        anchor = preview.events[0] if preview.events else self._ensure_test_event(schedule)
        payload_hash = _payload_hash(preview.card)
        key_material = report_idempotency_material(preview, test_send=test_send)
        idempotency_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        delivery_variant = f"report:{hashlib.sha256(key_material.encode('utf-8')).hexdigest()[:24]}"
        repo = DeliveryRepository(self.session)
        delivery = repo.ensure_pending(
            anchor,
            channel="feishu",
            target=schedule.destination.key,
            idempotency_key=idempotency_key,
            destination=schedule.destination,
            delivery_variant=delivery_variant,
            payload_hash=payload_hash,
        )
        if delivery.status == "delivered":
            _mark_schedule_run(
                schedule,
                window_start,
                window_end,
                status_time=utc_now(),
                result="duplicate",
                error=None,
            )
            return ReportSendOutcome(
                preview=preview,
                delivery=delivery,
                dry_run=delivery.provider_message_id == "dry-run",
                status="duplicate",
            )
        if not repo.claim_sending(delivery):
            _mark_schedule_run(
                schedule,
                window_start,
                window_end,
                status_time=utc_now(),
                result="duplicate",
                error=None,
            )
            return ReportSendOutcome(
                preview=preview,
                delivery=delivery,
                dry_run=delivery.provider_message_id == "dry-run",
                status="duplicate",
            )
        self.session.commit()
        try:
            result = await self._send(schedule.destination, preview.card, test_send=test_send)
        except Exception as exc:
            result = FeishuSendResult(
                ok=False,
                status_code=getattr(exc, "status_code", None),
                retry_after=_retry_after_from_exception(exc),
                error=_sanitize_error(str(exc) or "Feishu report delivery failed"),
            )
        if result.ok:
            repo.mark_delivered(
                delivery,
                provider_message_id=result.message_id or ("dry-run" if result.dry_run else None),
                response_status=result.status_code,
            )
            status = "sent"
            error = None
        else:
            error = _sanitize_error(result.error or "Feishu report delivery failed")
            repo.mark_failed(
                delivery,
                error,
                response_status=result.status_code,
                retry_after=result.retry_after,
            )
            status = "failed"
        _mark_schedule_run(
            schedule,
            window_start,
            window_end,
            status_time=utc_now(),
            result=status,
            error=error,
        )
        self.session.flush()
        return ReportSendOutcome(
            preview=preview,
            delivery=delivery,
            dry_run=result.dry_run,
            status=status,
            message=result.error,
        )

    async def send_test_report(self, schedule: ReportSchedule) -> ReportSendOutcome:
        now = utc_now()
        start = self.default_window_start(schedule, now)
        return await self.send_report_for_window(
            schedule,
            window_start=start,
            window_end=now,
            test_send=True,
        )

    def run_due_schedule(
        self,
        schedule: ReportSchedule,
        *,
        now: datetime | None = None,
    ) -> ReportSendOutcome | None:
        current = ensure_utc(now or utc_now()) or utc_now()
        if not schedule.enabled:
            return None
        scheduled_next_run = ensure_utc(schedule.next_run_at)
        if scheduled_next_run and scheduled_next_run > current:
            return None
        window_end = self._due_window_end(schedule, current)
        if _is_paired_daily_report(schedule):
            window_start = self.default_window_start(schedule, window_end)
        else:
            window_start = ensure_utc(schedule.last_window_end) or self.default_window_start(
                schedule, window_end
            )
        activated_at = ensure_utc(schedule.activated_at)
        if activated_at and window_start < activated_at:
            window_start = activated_at
        outcome = asyncio.run(
            self.send_report_for_window(
                schedule,
                window_start=window_start,
                window_end=window_end,
            )
        )
        schedule.next_run_at = next_run_at(schedule, after=window_end)
        self.session.flush()
        return outcome

    def events_for_window(
        self,
        schedule: ReportSchedule,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Event]:
        filters = _merged_filters(schedule, self._saved_search_filters(schedule.saved_search_id))
        query_limit = _query_event_limit(schedule)
        result_limit = _report_event_limit(schedule)
        query_window_start = _db_datetime(self.session, window_start)
        query_window_end = _db_datetime(self.session, window_end)
        stmt = (
            select(Event)
            .options(
                selectinload(Event.sources).selectinload(EventSource.source),
                selectinload(Event.ai_insights),
            )
            .where(Event.first_seen_at >= query_window_start)
            .where(Event.first_seen_at < query_window_end)
            .order_by(Event.first_seen_at.desc(), Event.id.desc())
            .limit(query_limit)
        )
        activated_at = ensure_utc(schedule.activated_at)
        if activated_at:
            stmt = stmt.where(Event.first_seen_at >= _db_datetime(self.session, activated_at))
        if filters["categories"]:
            stmt = stmt.where(Event.category.in_(filters["categories"]))
        if filters["severities"]:
            stmt = stmt.where(Event.severity.in_(filters["severities"]))
        if filters["minimum_trust_score"] is not None:
            stmt = stmt.where(Event.trust_score >= filters["minimum_trust_score"])
        events = list(self.session.scalars(stmt))
        filtered = [
            event
            for event in events
            if _event_matches_arrays(event, filters)
            and _event_matches_source_groups(event, filters["source_groups"])
            and event_priority_score(event) >= filters["minimum_priority_score"]
        ]
        filtered.sort(
            key=lambda event: (
                event_priority_score(event),
                SEVERITY_ORDER.get(event.severity, 0),
                event.published_at or event.first_seen_at,
                event.id,
            ),
            reverse=True,
        )
        return filtered[:result_limit]

    def default_window_start(self, schedule: ReportSchedule, window_end: datetime) -> datetime:
        if _is_paired_daily_report(schedule):
            start = _paired_daily_window_start(self.session, schedule, window_end)
        else:
            interval = schedule.interval_minutes or _default_interval_minutes(schedule.report_type)
            start = window_end - timedelta(minutes=interval)
        activated_at = ensure_utc(schedule.activated_at)
        if activated_at and start < activated_at:
            return activated_at
        return start

    def _due_window_end(self, schedule: ReportSchedule, now: datetime) -> datetime:
        if schedule.next_run_at:
            return min(ensure_utc(schedule.next_run_at) or now, now)
        return now

    async def _send(
        self,
        destination: NotificationDestination,
        card: dict[str, Any],
        *,
        test_send: bool,
    ) -> FeishuSendResult:
        if not settings.feishu_enabled or not settings.feishu_send_enabled:
            return FeishuSendResult(ok=True, message_id="dry-run", dry_run=True)
        if not destination.enabled or destination.status != "active":
            return FeishuSendResult(ok=False, error="Feishu destination is not active")
        if destination.provider == "feishu_app":
            if not destination.chat_id:
                return FeishuSendResult(ok=False, error="Feishu destination has no chat_id")
            client = self.client or FeishuClient()
            try:
                return await client.send_interactive_card(destination.chat_id, card)
            finally:
                if self.client is None:
                    await client.aclose()
        if destination.provider == "feishu_webhook":
            if self.encryptor is None or not destination.secret_ciphertext:
                return FeishuSendResult(
                    ok=False,
                    error="Feishu webhook encryption is not configured",
                )
            webhook_url = self.encryptor.decrypt(destination.secret_ciphertext)
            client = self.client or FeishuClient()
            try:
                return await client.send_custom_webhook(
                    webhook_url,
                    {"msg_type": "interactive", "card": card},
                    signing_secret=destination.config.get("signing_secret"),
                )
            finally:
                if self.client is None:
                    await client.aclose()
        return FeishuSendResult(ok=False, error="unsupported Feishu destination provider")

    def _ensure_test_event(self, schedule: ReportSchedule) -> Event:
        key = f"feishu-report-test:{schedule.id}"
        event = self.session.scalar(select(Event).where(Event.event_key == key))
        if event is not None:
            return event
        now = utc_now()
        event = Event(
            event_key=key,
            title="飞书汇报测试卡片",
            summary="这是一条用于发送测试汇报的系统事件，不代表真实新闻。",
            category="system",
            status="confirmed",
            severity="low",
            language="zh-CN",
            primary_url=settings.public_base_url,
            published_at=now,
            first_seen_at=now,
            last_seen_at=now,
            trust_score=100,
            confirmation_count=1,
            symbols=[],
            chains=[],
            entities=[],
            metadata_={"test": True, "report_schedule_id": str(schedule.id)},
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _saved_search_filters(self, saved_search_id: str | None) -> dict[str, Any]:
        if not saved_search_id:
            return {}
        try:
            saved_search = self.session.get(SavedSearch, int(saved_search_id))
        except (TypeError, ValueError):
            saved_search = None
        if saved_search is not None:
            value = getattr(saved_search, "filters", None) or getattr(saved_search, "query", None)
            return value if isinstance(value, dict) else {}
        bind = self.session.get_bind()
        if bind is None or not inspect(bind).has_table("saved_searches"):
            return {}
        table = sa.Table("saved_searches", sa.MetaData(), autoload_with=bind)
        id_column = table.c.get("id")
        if id_column is None:
            return {}
        filters_column = table.c.get("filters")
        if filters_column is None:
            filters_column = table.c.get("query")
        if filters_column is None:
            filters_column = table.c.get("criteria")
        if filters_column is None:
            return {}
        row = self.session.execute(
            select(filters_column).where(sa.cast(id_column, sa.Text()) == str(saved_search_id))
        ).first()
        if row is None:
            return {}
        value = row[0]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value if isinstance(value, dict) else {}


def due_schedules(session: Session, *, now: datetime | None = None) -> list[ReportSchedule]:
    current = ensure_utc(now or utc_now()) or utc_now()
    return list(
        session.scalars(
            select(ReportSchedule)
            .options(selectinload(ReportSchedule.destination))
            .where(ReportSchedule.enabled.is_(True))
            .where(
                (ReportSchedule.next_run_at.is_(None)) | (ReportSchedule.next_run_at <= current)
            )
            .order_by(ReportSchedule.next_run_at.asc().nullsfirst(), ReportSchedule.created_at)
            .limit(100)
        )
    )


def next_run_at(schedule: ReportSchedule, *, after: datetime | None = None) -> datetime:
    current = ensure_utc(after or utc_now()) or utc_now()
    if schedule.report_type in {"daily_morning", "daily_evening", "custom"}:
        zone = ZoneInfo(schedule.timezone or "UTC")
        local = current.astimezone(zone)
        hour = schedule.hour if schedule.hour is not None else 9
        minute = schedule.minute if schedule.minute is not None else 0
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)
    interval = schedule.interval_minutes or _default_interval_minutes(schedule.report_type)
    return current + timedelta(minutes=interval)


def report_idempotency_material(preview: ReportPreview, *, test_send: bool = False) -> str:
    schedule = preview.schedule
    filters = {
        "mode": "test" if test_send else "report",
        "destination_id": str(schedule.destination_id),
        "report_type": schedule.report_type,
        "saved_search_id": schedule.saved_search_id,
        "source_groups": sorted(schedule.source_groups),
        "categories": sorted(schedule.categories),
        "severities": sorted(schedule.severities),
        "symbols": sorted(schedule.symbols),
        "chains": sorted(schedule.chains),
        "minimum_trust_score": schedule.minimum_trust_score,
        "minimum_priority_score": _minimum_priority_score(schedule, {}),
        "include_ai_summary": schedule.include_ai_summary,
        "maximum_events": schedule.maximum_events,
        "window_start": preview.window_start.astimezone(UTC).isoformat(),
        "window_end": preview.window_end.astimezone(UTC).isoformat(),
    }
    if test_send:
        filters["test_invocation"] = utc_now().astimezone(UTC).isoformat()
    return json.dumps(filters, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _mark_schedule_run(
    schedule: ReportSchedule,
    window_start: datetime,
    window_end: datetime,
    *,
    status_time: datetime,
    result: str,
    error: str | None,
) -> None:
    schedule.last_window_start = window_start
    schedule.last_window_end = window_end
    schedule.last_run_at = status_time
    schedule.last_result = result
    schedule.last_error_sanitized = error


def _merged_filters(
    schedule: ReportSchedule, saved_search_filters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "source_groups": _list_filter(schedule.source_groups)
        or _list_filter(saved_search_filters.get("source_groups")),
        "categories": _list_filter(schedule.categories)
        or _list_filter(saved_search_filters.get("categories")),
        "severities": _list_filter(schedule.severities)
        or _list_filter(saved_search_filters.get("severities")),
        "symbols": _list_filter(schedule.symbols)
        or _list_filter(saved_search_filters.get("symbols")),
        "chains": _list_filter(schedule.chains)
        or _list_filter(saved_search_filters.get("chains")),
        "minimum_trust_score": schedule.minimum_trust_score
        if schedule.minimum_trust_score not in (None, 0)
        else saved_search_filters.get("minimum_trust_score"),
        "minimum_priority_score": _minimum_priority_score(schedule, saved_search_filters),
    }




def _is_daily_summary_report(schedule: ReportSchedule) -> bool:
    return schedule.report_type in {"daily_morning", "daily_evening", "custom"}


def _query_event_limit(schedule: ReportSchedule) -> int:
    base = max(schedule.maximum_events * 5, 50)
    if _is_daily_summary_report(schedule):
        return max(base, 200)
    return base


def _report_event_limit(schedule: ReportSchedule) -> int:
    if _is_daily_summary_report(schedule):
        return max(schedule.maximum_events, 50)
    return schedule.maximum_events


def _is_paired_daily_report(schedule: ReportSchedule) -> bool:
    return schedule.report_type in {"daily_morning", "daily_evening"}


def _paired_daily_window_start(
    session: Session, schedule: ReportSchedule, window_end: datetime
) -> datetime:
    paired = _paired_daily_schedule(session, schedule)
    zone = ZoneInfo(schedule.timezone or (paired.timezone if paired else "UTC") or "UTC")
    local_end = window_end.astimezone(zone)
    fallback_hour = 9 if schedule.report_type == "daily_evening" else 18
    start_hour = paired.hour if paired and paired.hour is not None else fallback_hour
    start_minute = paired.minute if paired and paired.minute is not None else 0
    start_local = local_end.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
    )
    if start_local >= local_end:
        start_local -= timedelta(days=1)
    return start_local.astimezone(UTC)


def _paired_daily_schedule(
    session: Session, schedule: ReportSchedule
) -> ReportSchedule | None:
    pair_type = (
        "daily_morning" if schedule.report_type == "daily_evening" else "daily_evening"
    )
    return session.scalar(
        select(ReportSchedule)
        .where(ReportSchedule.destination_id == schedule.destination_id)
        .where(ReportSchedule.report_type == pair_type)
        .where(ReportSchedule.enabled.is_(True))
        .order_by(ReportSchedule.created_at.desc(), ReportSchedule.id.desc())
    )


def _minimum_priority_score(
    schedule: ReportSchedule, saved_search_filters: dict[str, Any]
) -> int:
    configured = saved_search_filters.get("minimum_priority_score")
    if configured not in (None, ""):
        try:
            return max(0, min(100, int(configured)))
        except (TypeError, ValueError):
            pass
    explicit_filters = any(
        saved_search_filters.get(key)
        for key in ("source_groups", "categories", "severities", "symbols", "chains")
    )
    if explicit_filters:
        return 0
    return REPORT_DEFAULT_PRIORITY.get(schedule.report_type, 55)

def _list_filter(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple | set):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _event_matches_arrays(event: Event, filters: dict[str, Any]) -> bool:
    if filters["symbols"] and not _overlaps(event.symbols, filters["symbols"]):
        return False
    if filters["chains"] and not _overlaps(event.chains, filters["chains"]):
        return False
    return True


def _event_matches_source_groups(event: Event, source_groups: list[str]) -> bool:
    if not source_groups:
        return True
    wanted = {group.lower() for group in source_groups}
    for event_source in event.sources:
        source = event_source.source
        if not source:
            continue
        config = source.config or {}
        candidates = {
            str(source.source_group or "").lower(),
            str(config.get("source_group") or "").lower(),
            str(config.get("group") or "").lower(),
            str(source.source_type or "").lower(),
        }
        if candidates & wanted:
            return True
    return False


def _overlaps(values: list[str], filters: list[str]) -> bool:
    normalized = {value.upper() for value in values}
    return any(item.upper() in normalized for item in filters)


def _payload_hash(card: dict[str, Any]) -> str:
    raw = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _default_interval_minutes(report_type: str) -> int:
    return {
        "immediate": 5,
        "digest_15m": 15,
        "digest_30m": 30,
        "hourly": 60,
        "daily_morning": 24 * 60,
        "daily_evening": 24 * 60,
        "custom": 24 * 60,
    }.get(report_type, 60)


def _db_datetime(session: Session, value: datetime) -> datetime:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        return value.replace(tzinfo=None)
    return value


def _retry_after_from_exception(exc: BaseException) -> int | None:
    value = getattr(exc, "retry_after", None)
    return int(value) if isinstance(value, int) else None
