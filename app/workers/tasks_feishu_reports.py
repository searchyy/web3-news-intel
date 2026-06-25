from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.core.time import utc_now
from app.db.models import ReportSchedule
from app.db.session import SessionLocal
from app.integrations.feishu.reporting import FeishuReportService, due_schedules
from app.workers.celery_app import CELERY_REPORT_PRIORITY, CELERY_REPORT_QUEUE, celery_app


@celery_app.task(
    name="app.workers.tasks_feishu_reports.run_due_feishu_reports",
    queue=CELERY_REPORT_QUEUE,
    priority=CELERY_REPORT_PRIORITY,
)
def run_due_feishu_reports() -> dict[str, int]:
    with SessionLocal() as session:
        schedules = due_schedules(session, now=utc_now())
        processed = 0
        sent = 0
        empty = 0
        failed = 0
        service = FeishuReportService(session, encryptor=_field_encryptor_if_configured())
        for schedule in schedules:
            outcome = service.run_due_schedule(schedule, now=utc_now())
            if outcome is None:
                continue
            processed += 1
            if outcome.status in {"sent", "duplicate"}:
                sent += 1
            elif outcome.status == "empty":
                empty += 1
            else:
                failed += 1
        session.commit()
        return {"processed": processed, "sent": sent, "empty": empty, "failed": failed}


@celery_app.task(
    name="app.workers.tasks_feishu_reports.run_feishu_report_schedule",
    queue=CELERY_REPORT_QUEUE,
    priority=CELERY_REPORT_PRIORITY,
)
def run_feishu_report_schedule(schedule_id: str) -> dict[str, str | int | None]:
    with SessionLocal() as session:
        schedule = session.scalar(
            select(ReportSchedule)
            .options(selectinload(ReportSchedule.destination))
            .where(ReportSchedule.id == int(schedule_id))
        )
        if schedule is None:
            return {"status": "not_found", "delivery_id": None}
        service = FeishuReportService(session, encryptor=_field_encryptor_if_configured())
        outcome = service.run_due_schedule(schedule, now=utc_now())
        session.commit()
        if outcome is None:
            return {"status": "skipped", "delivery_id": None}
        return {
            "status": outcome.status,
            "delivery_id": outcome.delivery.id if outcome.delivery else None,
        }


@celery_app.task(
    name="app.workers.tasks_feishu_reports.send_feishu_report_test",
    queue=CELERY_REPORT_QUEUE,
    priority=CELERY_REPORT_PRIORITY,
)
def send_feishu_report_test(schedule_id: str) -> dict[str, str | int | None]:
    with SessionLocal() as session:
        schedule = session.scalar(
            select(ReportSchedule)
            .options(selectinload(ReportSchedule.destination))
            .where(ReportSchedule.id == int(schedule_id))
        )
        if schedule is None:
            return {"status": "not_found", "delivery_id": None}
        service = FeishuReportService(session, encryptor=_field_encryptor_if_configured())
        import asyncio

        outcome = asyncio.run(service.send_test_report(schedule))
        session.commit()
        return {
            "status": outcome.status,
            "delivery_id": outcome.delivery.id if outcome.delivery else None,
        }


def _field_encryptor_if_configured() -> FieldEncryptor | None:
    if not settings.field_encryption_key:
        return None
    return FieldEncryptor(settings.field_encryption_key)
