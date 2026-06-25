from __future__ import annotations

from app.workers.celery_app import (
    CELERY_AI_PRIORITY,
    CELERY_AI_QUEUE,
    CELERY_DEFAULT_QUEUE,
    CELERY_FETCH_PRIORITY,
    CELERY_FETCH_QUEUE,
    CELERY_PIPELINE_PRIORITY,
    CELERY_PIPELINE_QUEUE,
    CELERY_REPORT_PRIORITY,
    CELERY_REPORT_QUEUE,
    celery_app,
)
from app.workers.tasks_feishu_reports import (
    run_due_feishu_reports,
    run_feishu_report_schedule,
    send_feishu_report_test,
)
from app.workers.tasks_fetch import fetch_source, poll_sources
from app.workers.tasks_parse import parse_raw_document
from app.workers.tasks_publish import process_event_pipeline, republish_event
from app.workers.tasks_score import score_event


def test_celery_routes_split_fetch_pipeline_report_and_ai_queues() -> None:
    routes = celery_app.conf.task_routes

    assert routes["app.workers.tasks_feishu_reports.*"] == {
        "queue": CELERY_REPORT_QUEUE,
        "priority": CELERY_REPORT_PRIORITY,
    }
    assert routes["app.workers.tasks_parse.*"] == {
        "queue": CELERY_PIPELINE_QUEUE,
        "priority": CELERY_PIPELINE_PRIORITY,
    }
    assert routes["app.workers.tasks_publish.*"] == {
        "queue": CELERY_PIPELINE_QUEUE,
        "priority": CELERY_PIPELINE_PRIORITY,
    }
    assert routes["app.workers.tasks_score.*"] == {
        "queue": CELERY_PIPELINE_QUEUE,
        "priority": CELERY_PIPELINE_PRIORITY,
    }
    assert routes["app.workers.tasks_fetch.*"] == {
        "queue": CELERY_FETCH_QUEUE,
        "priority": CELERY_FETCH_PRIORITY,
    }
    assert routes["app.workers.tasks_ai.*"] == {
        "queue": CELERY_AI_QUEUE,
        "priority": CELERY_AI_PRIORITY,
    }


def test_celery_declares_report_first_queue_order() -> None:
    assert celery_app.conf.task_default_queue == CELERY_DEFAULT_QUEUE
    assert celery_app.conf.broker_transport_options["queue_order_strategy"] == "priority"
    assert [queue.name for queue in celery_app.conf.task_queues] == [
        CELERY_REPORT_QUEUE,
        CELERY_PIPELINE_QUEUE,
        CELERY_FETCH_QUEUE,
        CELERY_AI_QUEUE,
        CELERY_DEFAULT_QUEUE,
    ]
    assert CELERY_REPORT_PRIORITY < CELERY_PIPELINE_PRIORITY < CELERY_FETCH_PRIORITY


def test_task_decorators_pin_worker_queues() -> None:
    assert poll_sources.queue == CELERY_FETCH_QUEUE
    assert fetch_source.queue == CELERY_FETCH_QUEUE

    assert parse_raw_document.queue == CELERY_PIPELINE_QUEUE
    assert republish_event.queue == CELERY_PIPELINE_QUEUE
    assert process_event_pipeline.queue == CELERY_PIPELINE_QUEUE
    assert score_event.queue == CELERY_PIPELINE_QUEUE

    assert run_due_feishu_reports.queue == CELERY_REPORT_QUEUE
    assert run_feishu_report_schedule.queue == CELERY_REPORT_QUEUE
    assert send_feishu_report_test.queue == CELERY_REPORT_QUEUE


def test_beat_entries_publish_to_split_queues() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["poll-sources-every-minute"]["options"] == {
        "queue": CELERY_FETCH_QUEUE,
        "priority": CELERY_FETCH_PRIORITY,
    }
    assert schedule["run-feishu-reports-every-minute"]["options"] == {
        "queue": CELERY_REPORT_QUEUE,
        "priority": CELERY_REPORT_PRIORITY,
    }
    assert schedule["mark-stale-ai-jobs"]["options"] == {
        "queue": CELERY_AI_QUEUE,
        "priority": CELERY_AI_PRIORITY,
    }
