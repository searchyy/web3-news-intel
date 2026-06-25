from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.core.time import utc_now
from app.db.models import AIRun, Event, EventAIInsight
from app.db.repositories.event_repo import EventRepository
from app.db.repositories.notification_repo import NotificationRepository
from app.db.session import SessionLocal
from app.integrations.ai.runtime import get_ai_runtime_settings
from app.integrations.ai.service import (
    ACTIVE_JOB_STATUSES,
    AIService,
    auto_event_allowed,
    sanitize_error,
)
from app.pipeline.alert_rules import AlertEngine
from app.pipeline.destination_router import DestinationRouter
from app.publishers.base import DeliveryManager
from app.publishers.discord import DiscordPublisher
from app.publishers.feishu import publish_feishu_once
from app.publishers.telegram import TelegramPublisher
from app.publishers.webhook import WebhookPublisher
from app.workers.celery_app import CELERY_PIPELINE_PRIORITY, CELERY_PIPELINE_QUEUE, celery_app


@celery_app.task(
    name="app.workers.tasks_publish.republish_event",
    queue=CELERY_PIPELINE_QUEUE,
    priority=CELERY_PIPELINE_PRIORITY,
)
def republish_event(event_id: int) -> dict[str, int | str]:
    return asyncio.run(_republish_event(event_id))


@celery_app.task(
    name="app.workers.tasks_publish.process_event_pipeline",
    queue=CELERY_PIPELINE_QUEUE,
    priority=CELERY_PIPELINE_PRIORITY,
)
def process_event_pipeline(event_id: int) -> dict[str, int | str]:
    return asyncio.run(_process_event_pipeline(event_id))


async def _process_event_pipeline(event_id: int) -> dict[str, int | str]:
    ai_status = _enqueue_auto_ai_if_allowed(event_id)
    publish_result = await _republish_event(event_id)
    if publish_result.get("status") == "not_found":
        return {"status": "not_found", "event_id": event_id, "deliveries": 0}
    return {
        "status": "processed",
        "event_id": event_id,
        "ai_status": ai_status,
        "deliveries": int(publish_result.get("deliveries", 0)),
    }


def _enqueue_auto_ai_if_allowed(event_id: int) -> str:
    if not (settings.ai_enabled and settings.ai_auto_process_enabled):
        return "skipped"
    with SessionLocal() as session:
        event = EventRepository(session).get(event_id)
        if event is None:
            return "skipped:event_not_found"
        service = AIService(session)
        config = service.get_or_create_provider_config("deepseek")
        if not (config.enabled and config.auto_process_enabled):
            return "skipped:disabled"
        if not auto_event_allowed(event, config):
            return "skipped:below_auto_threshold"
        if _has_successful_ai_insight(session, event):
            return "skipped:already_summarized"
        budget_status = _auto_ai_budget_status(service, config)
        if budget_status is not None:
            return budget_status
        active = _active_auto_ai_job(session, event.id)
        if active is not None:
            return f"queued:existing:{active.id}"
        if not config.api_key_ciphertext or not config.model:
            return "skipped:ai_not_configured"
        task_id = uuid.uuid4().hex
        job = AIRun(
            job_type="summarize_event",
            provider=config.provider,
            model=config.model,
            event_count=1,
            event_ids=[event.id],
            status="queued",
            queued_at=utc_now(),
            task_id=task_id,
        )
        session.add(job)
        session.commit()
        try:
            from app.workers.tasks_ai import summarize_event as summarize_event_task

            runtime = get_ai_runtime_settings()
            summarize_event_task.apply_async(
                args=[job.id, event.id],
                kwargs={"force": False, "auto": True},
                queue=runtime.queue_name,
                task_id=task_id,
            )
        except Exception as exc:
            job.status = "failed"
            job.error_code = "ai_enqueue_failed"
            job.error_sanitized = sanitize_error(exc)
            job.error_message_sanitized = job.error_sanitized
            job.finished_at = utc_now()
            session.commit()
            return f"failed:{job.error_sanitized}"
        return f"queued:{job.id}"


def _auto_ai_budget_status(service: AIService, config) -> str | None:
    usage = service.usage_today(config.provider)
    if config.daily_token_budget > 0 and usage.tokens_today >= config.daily_token_budget:
        return "skipped:token_budget_exceeded"
    if config.daily_request_budget > 0 and usage.requests_today >= config.daily_request_budget:
        return "skipped:request_budget_exceeded"
    return None


def _has_successful_ai_insight(session, event: Event) -> bool:
    return (
        session.scalar(
            select(EventAIInsight.id)
            .where(EventAIInsight.event_id == event.id)
            .where(EventAIInsight.status.in_(["success", "succeeded"]))
            .limit(1)
        )
        is not None
    )


def _active_auto_ai_job(session, event_id: int) -> AIRun | None:
    runs = session.scalars(
        select(AIRun)
        .where(AIRun.job_type == "summarize_event")
        .where(AIRun.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(AIRun.created_at.desc())
        .limit(100)
    )
    for run in runs:
        if event_id in [int(item) for item in (run.event_ids or [])]:
            return run
    return None


async def _republish_event(event_id: int) -> dict[str, int | str]:
    with SessionLocal() as session:
        event = EventRepository(session).get(event_id)
        if event is None:
            return {"status": "not_found", "deliveries": 0}
        decision = AlertEngine().should_alert(event)
        if not decision.should_alert:
            return {"status": "skipped", "deliveries": 0}
        publishers = _configured_publishers()
        manager = DeliveryManager(session)
        deliveries = 0
        for publisher in publishers:
            await manager.publish_once(event, publisher)
            deliveries += 1
        router = DestinationRouter(session)
        encryptor = (
            FieldEncryptor(settings.field_encryption_key)
            if settings.field_encryption_key
            else None
        )
        for destination in NotificationRepository(session).active_destinations():
            decision = router.should_route(event, destination)
            if not decision.should_send:
                continue
            await publish_feishu_once(
                session,
                event,
                destination,
                encryptor=encryptor,
                delivery_variant=decision.delivery_mode,
            )
            deliveries += 1
        session.commit()
        return {"status": "published", "deliveries": deliveries}


def _configured_publishers():
    publishers = []
    if settings.alert_webhook_url:
        publishers.append(
            WebhookPublisher(
                settings.alert_webhook_url,
                secret=settings.alert_webhook_secret,
                allow_private_networks=settings.allow_private_networks,
                allow_localhost=settings.http_allow_localhost,
                validate_dns_rebinding=settings.http_validate_dns_rebinding,
            )
        )
    if settings.discord_webhook_url:
        publishers.append(
            DiscordPublisher(
                settings.discord_webhook_url,
                allow_private_networks=settings.allow_private_networks,
                allow_localhost=settings.http_allow_localhost,
                validate_dns_rebinding=settings.http_validate_dns_rebinding,
            )
        )
    if settings.telegram_bot_token and settings.telegram_chat_id:
        publishers.append(
            TelegramPublisher(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
        )
    return publishers
