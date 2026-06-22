from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.repositories.event_repo import EventRepository
from app.db.repositories.notification_repo import NotificationRepository
from app.db.session import SessionLocal
from app.integrations.ai.deepseek.errors import AIProviderError
from app.integrations.ai.service import AIService, sanitize_error
from app.pipeline.alert_rules import AlertEngine
from app.pipeline.destination_router import DestinationRouter
from app.publishers.base import DeliveryManager
from app.publishers.discord import DiscordPublisher
from app.publishers.feishu import publish_feishu_once
from app.publishers.telegram import TelegramPublisher
from app.publishers.webhook import WebhookPublisher
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_publish.republish_event")
def republish_event(event_id: int) -> dict[str, int | str]:
    return asyncio.run(_republish_event(event_id))


@celery_app.task(name="app.workers.tasks_publish.process_event_pipeline")
def process_event_pipeline(event_id: int) -> dict[str, int | str]:
    return asyncio.run(_process_event_pipeline(event_id))


async def _process_event_pipeline(event_id: int) -> dict[str, int | str]:
    ai_status = "skipped"
    with SessionLocal() as session:
        event = EventRepository(session).get(event_id)
        if event is None:
            return {"status": "not_found", "event_id": event_id, "deliveries": 0}
        config = AIService(session).get_or_create_provider_config("deepseek")
        should_auto_ai = bool(
            settings.ai_enabled
            and settings.ai_auto_process_enabled
            and config.enabled
            and config.auto_process_enabled
        )
        if should_auto_ai:
            try:
                await AIService(session).summarize_event(
                    event_id,
                    force=False,
                    auto=True,
                    job_type="summarize_event",
                    worker_name="pipeline",
                )
                session.commit()
                ai_status = "succeeded"
            except AIProviderError as exc:
                session.rollback()
                ai_status = f"failed:{sanitize_error(exc)}"
            except Exception as exc:
                session.rollback()
                ai_status = f"failed:{sanitize_error(exc)}"
    publish_result = await _republish_event(event_id)
    return {
        "status": "processed",
        "event_id": event_id,
        "ai_status": ai_status,
        "deliveries": int(publish_result.get("deliveries", 0)),
    }


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
