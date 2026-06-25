from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import settings
from app.integrations.ai.runtime import get_ai_runtime_settings

ai_runtime_settings = get_ai_runtime_settings()

CELERY_DEFAULT_QUEUE = "web3-news-intel"
CELERY_FETCH_QUEUE = "fetch"
CELERY_PIPELINE_QUEUE = "pipeline"
CELERY_REPORT_QUEUE = "report"
CELERY_AI_QUEUE = ai_runtime_settings.queue_name

# Celery's Redis transport consumes lower numeric priorities first.
CELERY_REPORT_PRIORITY = 0
CELERY_PIPELINE_PRIORITY = 3
CELERY_FETCH_PRIORITY = 6
CELERY_AI_PRIORITY = 6

celery_app = Celery(
    "web3_news_intel",
    broker=ai_runtime_settings.broker_url,
    backend=ai_runtime_settings.result_backend,
)
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
        "visibility_timeout": settings.celery_redis_visibility_timeout_seconds,
        "queue_order_strategy": "priority",
    },
    result_backend_transport_options={
        "visibility_timeout": settings.celery_redis_visibility_timeout_seconds
    },
    task_time_limit=300,
    task_soft_time_limit=240,
    task_default_retry_delay=5,
    task_default_queue=CELERY_DEFAULT_QUEUE,
    task_queues=(
        Queue(CELERY_REPORT_QUEUE),
        Queue(CELERY_PIPELINE_QUEUE),
        Queue(CELERY_FETCH_QUEUE),
        Queue(CELERY_AI_QUEUE),
        Queue(CELERY_DEFAULT_QUEUE),
    ),
    task_routes={
        "app.workers.tasks_feishu_reports.*": {
            "queue": CELERY_REPORT_QUEUE,
            "priority": CELERY_REPORT_PRIORITY,
        },
        "app.workers.tasks_parse.*": {
            "queue": CELERY_PIPELINE_QUEUE,
            "priority": CELERY_PIPELINE_PRIORITY,
        },
        "app.workers.tasks_publish.*": {
            "queue": CELERY_PIPELINE_QUEUE,
            "priority": CELERY_PIPELINE_PRIORITY,
        },
        "app.workers.tasks_score.*": {
            "queue": CELERY_PIPELINE_QUEUE,
            "priority": CELERY_PIPELINE_PRIORITY,
        },
        "app.workers.tasks_fetch.*": {
            "queue": CELERY_FETCH_QUEUE,
            "priority": CELERY_FETCH_PRIORITY,
        },
        "app.workers.tasks_ai.*": {
            "queue": CELERY_AI_QUEUE,
            "priority": CELERY_AI_PRIORITY,
        },
    },
    beat_schedule={
        "poll-sources-every-minute": {
            "task": "app.workers.tasks_fetch.poll_sources",
            "schedule": 30.0,
            "options": {"queue": CELERY_FETCH_QUEUE, "priority": CELERY_FETCH_PRIORITY},
        },
        "run-feishu-reports-every-minute": {
            "task": "app.workers.tasks_feishu_reports.run_due_feishu_reports",
            "schedule": 60.0,
            "options": {"queue": CELERY_REPORT_QUEUE, "priority": CELERY_REPORT_PRIORITY},
        },
        "mark-stale-ai-jobs": {
            "task": "app.workers.tasks_ai.mark_stale_ai_jobs",
            "schedule": 30.0,
            "options": {"queue": CELERY_AI_QUEUE, "priority": CELERY_AI_PRIORITY},
        },
    },
)

import app.workers.tasks_acceptance  # noqa: E402,F401

try:
    import app.workers.tasks_ai  # noqa: E402,F401
except (ImportError, ModuleNotFoundError):
    if getattr(settings, "ai_enabled", False):
        raise
import app.workers.tasks_feishu_reports  # noqa: E402,F401
import app.workers.tasks_fetch  # noqa: E402,F401
import app.workers.tasks_parse  # noqa: E402,F401
import app.workers.tasks_publish  # noqa: E402,F401
import app.workers.tasks_score  # noqa: E402,F401
