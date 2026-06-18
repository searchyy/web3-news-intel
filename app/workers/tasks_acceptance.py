from __future__ import annotations

import time
from typing import Any

import redis
from celery.exceptions import Ignore
from sqlalchemy import select

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import Delivery, Event, Source
from app.db.repositories.delivery_repo import DeliveryRepository
from app.db.session import SessionLocal
from app.publishers.base import delivery_idempotency_key
from app.workers.celery_app import celery_app


def _assert_acceptance_enabled() -> None:
    if not settings.enable_acceptance_tasks and settings.app_env not in {"test", "ci"}:
        raise Ignore()


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.create_event_once",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def create_event_once(self, acceptance_key: str) -> dict[str, Any]:
    _assert_acceptance_enabled()
    return _ensure_acceptance_result(acceptance_key)


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.transient_retry_once",
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def transient_retry_once(self, acceptance_key: str) -> dict[str, Any]:
    _assert_acceptance_enabled()
    redis_client = _redis()
    attempts = int(redis_client.incr(f"acceptance:{acceptance_key}:transient_attempts"))
    if attempts == 1:
        raise self.retry(exc=ConnectionError("deterministic transient failure"), countdown=0)
    result = _ensure_acceptance_result(acceptance_key)
    result["retry_attempts"] = attempts
    return result


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.worker_loss_idempotent",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
)
def worker_loss_idempotent(
    self, acceptance_key: str, *, hold_first_attempt_seconds: float = 30.0
) -> dict[str, Any]:
    _assert_acceptance_enabled()
    redis_client = _redis()
    attempts = int(redis_client.incr(f"acceptance:{acceptance_key}:worker_loss_attempts"))
    redis_client.set(f"acceptance:{acceptance_key}:started", str(attempts), ex=120)
    if attempts == 1:
        time.sleep(hold_first_attempt_seconds)
    result = _ensure_acceptance_result(acceptance_key)
    result["worker_loss_attempts"] = attempts
    return result


def _ensure_acceptance_result(acceptance_key: str) -> dict[str, Any]:
    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.key == "acceptance_fixture"))
        if source is None:
            source = Source(
                key="acceptance_fixture",
                name="Acceptance Fixture",
                source_type="acceptance",
                adapter="rss",
                url="https://example.com/acceptance.xml",
                canonical_url="https://example.com/acceptance.xml",
                category="security",
                language="en",
                trust_score=100,
                poll_seconds=300,
                timeout_seconds=5,
                max_response_bytes=65536,
                enabled=True,
                allow_private_networks=False,
                allow_localhost=False,
                config={"parser_version": "acceptance_v1"},
            )
            session.add(source)
            session.flush()

        event_key = f"acceptance:{acceptance_key}"
        event = session.scalar(select(Event).where(Event.event_key == event_key))
        if event is None:
            event = Event(
                event_key=event_key,
                title=f"Acceptance event {acceptance_key}",
                category="security",
                status="confirmed",
                severity="high",
                language="en",
                primary_url="https://example.com/acceptance",
                published_at=utc_now(),
                trust_score=100,
                confirmation_count=1,
                symbols=[],
                chains=[],
                entities=[],
                metadata_={"acceptance_key": acceptance_key},
            )
            session.add(event)
            session.flush()

        delivery_key = delivery_idempotency_key(event, "webhook", "acceptance://fixture")
        delivery = DeliveryRepository(session).ensure_pending(
            event,
            channel="webhook",
            target="acceptance://fixture",
            idempotency_key=delivery_key,
        )
        if delivery.status != "delivered":
            DeliveryRepository(session).mark_delivered(delivery)
        session.commit()

        event_count = session.query(Event).filter(Event.event_key == event_key).count()
        delivery_count = (
            session.query(Delivery).filter(Delivery.idempotency_key == delivery_key).count()
        )
        return {
            "status": "success",
            "event_key": event_key,
            "event_count": event_count,
            "delivery_count": delivery_count,
        }
