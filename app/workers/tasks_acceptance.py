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
from app.integrations.ai.deepseek.errors import AIBudgetExceededError, AIRateLimitedError
from app.integrations.ai.limits import ai_limit_controller
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


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.ai_limit_hold",
    max_retries=0,
)
def ai_limit_hold(
    self,
    acceptance_key: str,
    *,
    hold_seconds: float = 1.0,
    max_concurrency: int = 1,
    requests_per_minute: int = 60,
) -> dict[str, Any]:
    del self
    _assert_acceptance_enabled()
    provider = f"acceptance-{acceptance_key}"
    redis_client = _redis()
    try:
        with ai_limit_controller.reserve(
            provider,
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
            daily_request_budget=0,
            daily_token_budget=0,
        ):
            redis_client.set(f"acceptance:{acceptance_key}:ai_limit_started", "1", ex=120)
            time.sleep(hold_seconds)
            return {"status": "success", "provider": provider}
    except AIRateLimitedError as exc:
        return {
            "status": "rate_limited",
            "provider": provider,
            "retry_after_seconds": int(getattr(exc, "retry_after_seconds", 1) or 1),
        }


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.ai_request_budget_probe",
    max_retries=0,
)
def ai_request_budget_probe(
    self,
    acceptance_key: str,
    *,
    daily_request_budget: int = 1,
) -> dict[str, Any]:
    del self
    _assert_acceptance_enabled()
    provider = f"acceptance-budget-{acceptance_key}"
    try:
        with ai_limit_controller.reserve(
            provider,
            max_concurrency=1,
            requests_per_minute=60,
            daily_request_budget=daily_request_budget,
            daily_token_budget=0,
        ):
            return {"status": "success", "provider": provider}
    except AIBudgetExceededError as exc:
        return {"status": "budget_rejected", "provider": provider, "error": str(exc)}


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.ai_input_hash_once",
    max_retries=5,
)
def ai_input_hash_once(
    self,
    acceptance_key: str,
    *,
    hold_seconds: float = 1.0,
) -> dict[str, Any]:
    _assert_acceptance_enabled()
    provider = f"acceptance-dedupe-{acceptance_key}"
    redis_client = _redis()
    generated_key = f"acceptance:{acceptance_key}:ai_generated"
    if redis_client.get(generated_key) is not None:
        return {"status": "cached", "generated_count": int(redis_client.get(generated_key) or "1")}
    try:
        with ai_limit_controller.input_hash_lock(
            f"{provider}:deepseek-chat:1:v1:fixed-input-hash",
            ttl_seconds=30,
        ):
            if redis_client.get(generated_key) is not None:
                return {
                    "status": "cached",
                    "generated_count": int(redis_client.get(generated_key) or "1"),
                }
            time.sleep(hold_seconds)
            generated_count = int(redis_client.incr(generated_key))
            redis_client.expire(generated_key, 120)
            return {"status": "generated", "generated_count": generated_count}
    except AIRateLimitedError as exc:
        raise self.retry(
            exc=exc,
            countdown=int(getattr(exc, "retry_after_seconds", None) or 1),
        ) from exc


@celery_app.task(
    bind=True,
    name="app.workers.tasks_acceptance.record_if_executed",
    max_retries=0,
)
def record_if_executed(self, acceptance_key: str) -> dict[str, Any]:
    del self
    _assert_acceptance_enabled()
    redis_client = _redis()
    redis_client.set(f"acceptance:{acceptance_key}:executed", "1", ex=120)
    return {"status": "executed"}


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
