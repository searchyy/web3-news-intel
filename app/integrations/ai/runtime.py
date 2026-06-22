from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import redis
from celery import Celery

from app.core.config import settings
from app.integrations.ai.deepseek.errors import AIProviderError

AIExecutionMode = Literal["sync", "async"]
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


class AIRuntimeError(AIProviderError):
    error_code = "ai_runtime_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code


class AISyncModeForbiddenError(AIRuntimeError):
    error_code = "ai_sync_mode_forbidden"


class AIRuntimeUnavailableError(AIRuntimeError):
    error_code = "ai_runtime_unavailable"


class AIRuntimeTimeoutError(AIRuntimeError):
    error_code = "ai_runtime_timeout"


@dataclass(frozen=True)
class AIRuntimeSettings:
    execution_mode: AIExecutionMode
    sync_allowed_environments: set[str]
    sync_timeout_seconds: int
    queue_name: str
    job_stuck_seconds: int
    job_timeout_seconds: int
    broker_url: str
    result_backend: str
    app_env: str

    @property
    def task_soft_time_limit_seconds(self) -> int:
        return max(1, self.job_timeout_seconds - 5)


@dataclass(frozen=True)
class AIRuntimeHealth:
    ok: bool
    status: str
    detail: str | None = None
    worker_names: list[str] | None = None


@dataclass(slots=True)
class AIRuntimeStatus:
    execution_mode: str
    sync_allowed: bool
    redis_ok: bool
    worker_ok: bool
    queue_name: str
    detail: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ready(self) -> bool:
        return (self.execution_mode == "sync" and self.sync_allowed) or (
            self.redis_ok and self.worker_ok
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "sync_allowed": self.sync_allowed,
            "redis_ok": self.redis_ok,
            "worker_ok": self.worker_ok,
            "queue_name": self.queue_name,
            "ready": self.ready,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
        }


def get_ai_runtime_settings() -> AIRuntimeSettings:
    mode = _env("AI_EXECUTION_MODE", str(settings.ai_execution_mode)).strip().lower()
    if mode not in {"sync", "async"}:
        raise AIRuntimeError("AI_EXECUTION_MODE 只能是 sync 或 async")
    allowed_raw = _env("AI_SYNC_ALLOWED_ENVIRONMENTS", settings.ai_sync_allowed_environments)
    allowed = {item.strip().lower() for item in allowed_raw.split(",") if item.strip()}
    broker_url = _env(
        "CELERY_BROKER_URL",
        settings.celery_broker_url or settings.redis_url,
    ).strip()
    result_backend = _env(
        "CELERY_RESULT_BACKEND",
        settings.celery_result_backend or broker_url,
    ).strip()
    return AIRuntimeSettings(
        execution_mode=mode,  # type: ignore[arg-type]
        sync_allowed_environments=allowed,
        sync_timeout_seconds=_int_env(
            "AI_SYNC_TIMEOUT_SECONDS",
            settings.ai_sync_timeout_seconds,
            minimum=1,
        ),
        queue_name=_env("AI_QUEUE_NAME", settings.ai_queue_name).strip() or "ai",
        job_stuck_seconds=_int_env(
            "AI_JOB_STUCK_SECONDS",
            settings.ai_job_stuck_seconds,
            minimum=1,
        ),
        job_timeout_seconds=_int_env(
            "AI_JOB_TIMEOUT_SECONDS",
            settings.ai_job_timeout_seconds,
            minimum=1,
        ),
        broker_url=broker_url,
        result_backend=result_backend,
        app_env=_env("APP_ENV", settings.app_env).strip().lower() or "local",
    )


def validate_sync_execution_allowed(
    runtime_settings: AIRuntimeSettings | None = None,
) -> AIRuntimeSettings:
    runtime_settings = runtime_settings or get_ai_runtime_settings()
    if runtime_settings.execution_mode != "sync":
        return runtime_settings
    if runtime_settings.app_env in {"production", "staging"}:
        raise AISyncModeForbiddenError("production/staging 环境禁止使用 AI 同步执行模式")
    if runtime_settings.app_env not in runtime_settings.sync_allowed_environments:
        allowed = ", ".join(sorted(runtime_settings.sync_allowed_environments)) or "无"
        raise AISyncModeForbiddenError(
            f"当前环境 {runtime_settings.app_env} 不允许 AI 同步执行，允许环境：{allowed}"
        )
    return runtime_settings


def sync_allowed_for_current_environment() -> bool:
    try:
        validate_sync_execution_allowed(_with_execution_mode("sync"))
    except AISyncModeForbiddenError:
        return False
    return True


def require_sync_allowed() -> None:
    validate_sync_execution_allowed(_with_execution_mode("sync"))


def ai_runtime_status(
    *, check_worker: bool = True, celery_app: Celery | None = None
) -> AIRuntimeStatus:
    runtime_settings = get_ai_runtime_settings()
    sync_allowed = sync_allowed_for_current_environment()
    if runtime_settings.execution_mode == "sync":
        detail = None if sync_allowed else "当前环境不允许同步执行 AI 摘要"
        return AIRuntimeStatus(
            execution_mode="sync",
            sync_allowed=sync_allowed,
            redis_ok=False,
            worker_ok=False,
            queue_name=runtime_settings.queue_name,
            detail=detail,
        )

    redis_health = check_redis_health(runtime_settings.broker_url)
    worker_health = AIRuntimeHealth(ok=False, status="worker_not_checked")
    if redis_health.ok and check_worker:
        worker_health = check_worker_health(
            celery_app or _import_celery_app(),
            queue_name=runtime_settings.queue_name,
            require_queue=True,
        )
    return AIRuntimeStatus(
        execution_mode="async",
        sync_allowed=sync_allowed,
        redis_ok=redis_health.ok,
        worker_ok=worker_health.ok,
        queue_name=runtime_settings.queue_name,
        detail=redis_health.detail or worker_health.detail,
    )


def ensure_async_runtime_ready(celery_app: Celery | None = None) -> AIRuntimeStatus:
    status = ai_runtime_status(celery_app=celery_app)
    if not status.redis_ok:
        raise AIRuntimeError(
            "Redis 不可用，AI 异步任务无法入队，请检查 REDIS_URL/CELERY_BROKER_URL",
            error_code="ai_redis_unavailable",
        )
    if not status.worker_ok:
        raise AIRuntimeError(
            "Celery Worker 未运行或心跳已过期，AI 任务不会被消费",
            error_code="ai_worker_unavailable",
        )
    return status


def ensure_async_runtime_available(
    celery_app: Celery,
    runtime_settings: AIRuntimeSettings | None = None,
    *,
    inspect_timeout_seconds: float = 1.0,
    require_queue: bool = True,
    redis_health: AIRuntimeHealth | None = None,
    worker_health: AIRuntimeHealth | None = None,
) -> AIRuntimeHealth:
    runtime_settings = runtime_settings or get_ai_runtime_settings()
    if runtime_settings.execution_mode != "async":
        return AIRuntimeHealth(ok=True, status="sync_mode")
    redis_health = redis_health or check_redis_health(runtime_settings.broker_url)
    if not redis_health.ok:
        raise AIRuntimeUnavailableError(redis_health.detail or "Redis 不可用，无法提交 AI 异步任务")
    worker_health = worker_health or check_worker_health(
        celery_app,
        queue_name=runtime_settings.queue_name,
        timeout_seconds=inspect_timeout_seconds,
        require_queue=require_queue,
    )
    if not worker_health.ok:
        raise AIRuntimeUnavailableError(
            worker_health.detail or "Celery Worker 未运行或心跳已过期，无法提交 AI 异步任务"
        )
    return worker_health


def check_redis_health(redis_url: str, *, timeout_seconds: float = 1.0) -> AIRuntimeHealth:
    if not redis_url:
        return AIRuntimeHealth(ok=False, status="redis_unconfigured", detail="Redis 未配置")
    client: redis.Redis | None = None
    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )
        client.ping()
    except Exception:
        return AIRuntimeHealth(ok=False, status="redis_unavailable", detail="Redis 不可用")
    finally:
        if client is not None:
            client.close()
    return AIRuntimeHealth(ok=True, status="redis_ok")


def check_worker_health(
    celery_app: Celery,
    *,
    queue_name: str,
    timeout_seconds: float = 1.0,
    require_queue: bool = True,
) -> AIRuntimeHealth:
    try:
        inspector = celery_app.control.inspect(timeout=timeout_seconds)
        ping_replies = inspector.ping() or {}
    except Exception:
        return AIRuntimeHealth(
            ok=False,
            status="worker_unavailable",
            detail="Celery Worker 未运行或心跳已过期",
        )
    worker_names = sorted(str(name) for name in ping_replies)
    if not worker_names:
        return AIRuntimeHealth(
            ok=False,
            status="worker_unavailable",
            detail="Celery Worker 未运行或心跳已过期",
        )
    if require_queue:
        try:
            active_queues = inspector.active_queues() or {}
        except Exception:
            return AIRuntimeHealth(
                ok=False,
                status="worker_queue_unknown",
                detail=f"无法确认 Celery Worker 是否监听 AI 队列：{queue_name}",
                worker_names=worker_names,
            )
        if not active_queues:
            return AIRuntimeHealth(
                ok=False,
                status="worker_queue_unknown",
                detail=f"无法确认 Celery Worker 是否监听 AI 队列：{queue_name}",
                worker_names=worker_names,
            )
        listening = [
            worker
            for worker, queues in active_queues.items()
            if any(_queue_matches(queue, queue_name) for queue in queues or [])
        ]
        if not listening:
            return AIRuntimeHealth(
                ok=False,
                status="worker_queue_unavailable",
                detail=f"Celery Worker 未监听 AI 队列：{queue_name}",
                worker_names=worker_names,
            )
    return AIRuntimeHealth(ok=True, status="worker_ok", worker_names=worker_names)


def celery_broker_url() -> str:
    return get_ai_runtime_settings().broker_url


def celery_result_backend() -> str:
    return get_ai_runtime_settings().result_backend


def _queue_matches(queue: Any, queue_name: str) -> bool:
    if isinstance(queue, dict):
        return str(queue.get("name") or "") == queue_name
    return str(queue) == queue_name


def _with_execution_mode(execution_mode: AIExecutionMode) -> AIRuntimeSettings:
    runtime_settings = get_ai_runtime_settings()
    return AIRuntimeSettings(
        execution_mode=execution_mode,
        sync_allowed_environments=runtime_settings.sync_allowed_environments,
        sync_timeout_seconds=runtime_settings.sync_timeout_seconds,
        queue_name=runtime_settings.queue_name,
        job_stuck_seconds=runtime_settings.job_stuck_seconds,
        job_timeout_seconds=runtime_settings.job_timeout_seconds,
        broker_url=runtime_settings.broker_url,
        result_backend=runtime_settings.result_backend,
        app_env=runtime_settings.app_env,
    )


def _import_celery_app() -> Celery:
    from app.workers.celery_app import celery_app

    return celery_app


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AIRuntimeError(f"{name} 必须是整数") from exc
    if value < minimum:
        raise AIRuntimeError(f"{name} 必须大于等于 {minimum}")
    return value
