from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery("web3_news_intel", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": settings.celery_redis_visibility_timeout_seconds
    },
    result_backend_transport_options={
        "visibility_timeout": settings.celery_redis_visibility_timeout_seconds
    },
    task_time_limit=300,
    task_soft_time_limit=240,
    task_default_retry_delay=5,
    task_default_queue="web3-news-intel",
    beat_schedule={
        "poll-sources-every-minute": {
            "task": "app.workers.tasks_fetch.poll_sources",
            "schedule": 60.0,
        }
    },
)

import app.workers.tasks_acceptance  # noqa: E402,F401
import app.workers.tasks_fetch  # noqa: E402,F401
import app.workers.tasks_parse  # noqa: E402,F401
import app.workers.tasks_publish  # noqa: E402,F401
import app.workers.tasks_score  # noqa: E402,F401
