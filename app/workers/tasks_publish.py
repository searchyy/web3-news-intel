from __future__ import annotations

import asyncio

from app.core.config import settings
from app.db.repositories.event_repo import EventRepository
from app.db.session import SessionLocal
from app.pipeline.alert_rules import AlertEngine
from app.publishers.base import DeliveryManager
from app.publishers.discord import DiscordPublisher
from app.publishers.telegram import TelegramPublisher
from app.publishers.webhook import WebhookPublisher
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_publish.republish_event")
def republish_event(event_id: int) -> dict[str, int | str]:
    return asyncio.run(_republish_event(event_id))


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
