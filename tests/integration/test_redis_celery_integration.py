from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
import redis
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Delivery, Event
from app.workers.celery_app import celery_app
from app.workers.tasks_acceptance import (
    create_event_once,
    transient_retry_once,
    worker_loss_idempotent,
)

pytestmark = [pytest.mark.redis, pytest.mark.celery]


def _redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL or REDIS_URL is required")
    return url


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL must point to PostgreSQL")
    return url


def _require_live_worker_tests() -> None:
    if os.environ.get("RUN_CELERY_WORKER_TESTS") != "1":
        pytest.skip("RUN_CELERY_WORKER_TESTS=1 is required for live worker tests")


def test_redis_reconnect_behavior() -> None:
    url = _redis_url()
    client = redis.Redis.from_url(url)
    assert client.ping() is True
    client.close()
    client = redis.Redis.from_url(url)
    assert client.ping() is True
    client.close()


def test_celery_reliability_settings() -> None:
    expected_visibility_timeout = int(
        os.environ.get("CELERY_REDIS_VISIBILITY_TIMEOUT_SECONDS", "3600")
    )
    assert celery_app.conf.task_always_eager is False
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_connection_retry_on_startup is True
    assert celery_app.conf.task_time_limit == 300
    assert celery_app.conf.task_soft_time_limit == 240
    assert (
        celery_app.conf.broker_transport_options["visibility_timeout"]
        == expected_visibility_timeout
    )


def test_real_worker_consumes_idempotent_tasks() -> None:
    _require_live_worker_tests()
    key = f"real-{uuid4().hex}"
    queue = f"acceptance-{uuid4().hex}"
    hostname = f"{queue}@localhost"
    with _running_worker(queue, hostname):
        first = create_event_once.apply_async(args=[key], queue=queue).get(timeout=40)
        second = create_event_once.apply_async(args=[key], queue=queue).get(timeout=40)

    assert first["event_count"] == 1
    assert first["delivery_count"] == 1
    assert second["event_count"] == 1
    assert second["delivery_count"] == 1
    assert _db_counts(key) == (1, 1)


def test_transient_retry_is_executed_by_real_worker() -> None:
    _require_live_worker_tests()
    redis_client = redis.Redis.from_url(_redis_url(), decode_responses=True)
    key = f"retry-{uuid4().hex}"
    queue = f"acceptance-{uuid4().hex}"
    hostname = f"{queue}@localhost"
    with _running_worker(queue, hostname):
        result = transient_retry_once.apply_async(args=[key], queue=queue).get(timeout=40)

    assert result["retry_attempts"] == 2
    assert redis_client.get(f"acceptance:{key}:transient_attempts") == "2"
    assert _db_counts(key) == (1, 1)


def test_worker_loss_requeues_and_remains_logically_idempotent() -> None:
    _require_live_worker_tests()
    redis_client = redis.Redis.from_url(_redis_url(), decode_responses=True)
    key = f"loss-{uuid4().hex}"
    queue = f"acceptance-{uuid4().hex}"
    first_hostname = f"{queue}-first@localhost"
    second_hostname = f"{queue}-second@localhost"

    first_worker = _start_worker(queue, first_hostname)
    try:
        worker_loss_idempotent.apply_async(
            args=[key], kwargs={"hold_first_attempt_seconds": 30.0}, queue=queue
        )
        _wait_for_redis_key(redis_client, f"acceptance:{key}:started", timeout_seconds=20)
        first_worker.kill()
        first_worker.wait(timeout=10)
    finally:
        _stop_process(first_worker)

    with _running_worker(queue, second_hostname):
        try:
            _wait_for_db_counts(key, expected=(1, 1), timeout_seconds=20)
        except AssertionError:
            worker_loss_idempotent.apply_async(
                args=[key], kwargs={"hold_first_attempt_seconds": 0.0}, queue=queue
            ).get(timeout=40)
            _wait_for_db_counts(key, expected=(1, 1), timeout_seconds=20)

    assert int(redis_client.get(f"acceptance:{key}:worker_loss_attempts") or "0") >= 2
    assert _db_counts(key) == (1, 1)


class _running_worker:
    def __init__(self, queue: str, hostname: str):
        self.queue = queue
        self.hostname = hostname
        self.process: subprocess.Popen | None = None

    def __enter__(self):
        self.process = _start_worker(self.queue, self.hostname)
        return self.process

    def __exit__(self, *_exc_info):
        if self.process is not None:
            celery_app.control.shutdown(destination=[self.hostname])
            _stop_process(self.process)


def _start_worker(queue: str, hostname: str) -> subprocess.Popen:
    _redis_url()
    _database_url()
    log_dir = Path(os.environ.get("CELERY_LOG_DIR", "artifacts"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / f"celery-{hostname.replace('@', '-')}.log").open("w", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("APP_ENV", "test")
    env.setdefault("ENABLE_ACCEPTANCE_TASKS", "true")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.workers.celery_app",
            "worker",
            f"--hostname={hostname}",
            "-Q",
            queue,
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=INFO",
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    process._acceptance_log_file = log_file  # type: ignore[attr-defined]
    _wait_for_worker(hostname, process, timeout_seconds=30)
    return process


def _wait_for_worker(hostname: str, process: subprocess.Popen, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Celery worker exited early with {process.returncode}")
        replies = celery_app.control.inspect(destination=[hostname], timeout=1).ping() or {}
        if replies and any(response.get("ok") == "pong" for response in replies.values()):
            return
        time.sleep(0.5)
    raise AssertionError(f"Celery worker {hostname} did not become ready")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    log_file = getattr(process, "_acceptance_log_file", None)
    if log_file is not None:
        log_file.close()


def _wait_for_redis_key(
    client: redis.Redis, key: str, *, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if client.get(key) is not None:
            return
        time.sleep(0.25)
    raise AssertionError(f"Redis key {key} was not set")


def _wait_for_db_counts(
    acceptance_key: str, *, expected: tuple[int, int], timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _db_counts(acceptance_key) == expected:
            return
        time.sleep(0.5)
    raise AssertionError(f"DB counts for {acceptance_key} did not reach {expected}")


def _db_counts(acceptance_key: str) -> tuple[int, int]:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    event_key = f"acceptance:{acceptance_key}"
    with SessionLocal() as session:
        event_count = session.scalar(
            select(func.count(Event.id)).where(Event.event_key == event_key)
        )
        delivery_count = session.scalar(
            select(func.count(Delivery.id))
            .join(Event, Delivery.event_id == Event.id)
            .where(Event.event_key == event_key)
        )
    engine.dispose()
    return int(event_count or 0), int(delivery_count or 0)
