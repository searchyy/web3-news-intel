from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, object_session, selectinload

from app.core.config import settings
from app.core.field_encryption import (
    FieldEncryptionError,
    FieldEncryptor,
    fingerprint_secret,
    mask_fingerprint,
)
from app.core.time import ensure_utc, utc_now
from app.db.models import (
    AIPromptTemplate,
    AIProviderConfig,
    AIRun,
    Event,
    EventAIInsight,
    EventSource,
)
from app.integrations.ai.base import AIMessage, AIProvider, AIProviderRuntimeConfig
from app.integrations.ai.deepseek.errors import (
    AIBudgetExceededError,
    AIJSONValidationError,
    AIProviderError,
    AITransientError,
)
from app.integrations.ai.input_builder import build_event_input as build_ai_event_input
from app.integrations.ai.limits import ai_limit_controller
from app.integrations.ai.prompts import (
    DEFAULT_OUTPUT_SCHEMA_VERSION,
    DEFAULT_PROMPT_KEY,
    DEFAULT_PROMPT_VERSION,
    REPAIR_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)
from app.integrations.ai.registry import registry
from app.integrations.ai.runtime import AIRuntimeTimeoutError, get_ai_runtime_settings
from app.integrations.ai.schemas import (
    AIEventInput,
    AIInsightOutput,
    AIUsageSnapshot,
    parse_json_object,
)
from app.integrations.ai.settings import (
    DEEPSEEK_OFFICIAL_API_BASE,
    ai_settings,
    validate_deepseek_api_base,
)
from app.pipeline.scoring import event_priority_score

MASKED_KEY_PREFIXES = ("****", "sha256:")
ACTIVE_JOB_STATUSES = {"queued", "started", "retrying"}
MISSING_FIELD_ENCRYPTION_KEY_MESSAGE = (
    "缺少 FIELD_ENCRYPTION_KEY，无法加密保存或读取 DeepSeek API Key，请先配置后端加密密钥。"
)
INVALID_FIELD_ENCRYPTION_KEY_MESSAGE = (
    "FIELD_ENCRYPTION_KEY 配置无效，无法加密保存或读取 DeepSeek API Key。"
)
SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "header",
    "raw",
    "html",
    "body",
)


class AIConfigurationError(AIProviderError):
    error_code = "ai_configuration_error"


class AIJobCancelledError(AIProviderError):
    error_code = "ai_job_cancelled"


class AIJobStoppedError(AIProviderError):
    error_code = "ai_job_stopped"


class AIService:
    def __init__(
        self,
        session: Session,
        *,
        provider_registry=registry,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.session = session
        self.provider_registry = provider_registry
        self.http_client = http_client

    def get_or_create_provider_config(self, provider: str = "deepseek") -> AIProviderConfig:
        row = self.session.scalar(
            select(AIProviderConfig).where(AIProviderConfig.provider == provider)
        )
        if row is not None:
            return row
        if provider != "deepseek":
            raise AIConfigurationError("unsupported AI provider")
        row = AIProviderConfig(
            provider="deepseek",
            enabled=ai_settings.ai_enabled,
            api_base=ai_settings.deepseek_api_base.rstrip("/"),
            timeout_seconds=ai_settings.deepseek_request_timeout_seconds,
            max_concurrency=ai_settings.deepseek_max_concurrency,
            max_tokens=1200,
            temperature=0.2,
            daily_token_budget=ai_settings.deepseek_daily_token_budget,
            auto_process_enabled=ai_settings.ai_auto_process_enabled,
            auto_minimum_severity="high",
            config={"requests_per_minute": 60},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def usage_today(self, provider: str = "deepseek") -> AIUsageSnapshot:
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        failure_case = case((AIRun.status.in_(["failed", "budget_rejected"]), 1), else_=0)
        row = self.session.execute(
            select(
                func.coalesce(func.sum(AIRun.prompt_tokens + AIRun.completion_tokens), 0),
                func.count(AIRun.id),
                func.coalesce(func.sum(failure_case), 0),
            ).where(AIRun.provider == provider, AIRun.created_at >= since)
        ).one()
        return AIUsageSnapshot(
            tokens_today=int(row[0] or 0),
            requests_today=int(row[1] or 0),
            failures_today=int(row[2] or 0),
        )

    def save_provider_config(
        self,
        values: dict[str, Any],
        *,
        provider: str = "deepseek",
    ) -> AIProviderConfig:
        row = self.get_or_create_provider_config(provider)
        api_base = str(values.get("api_base") or row.api_base or DEEPSEEK_OFFICIAL_API_BASE)
        allow_custom = bool(ai_settings.ai_allow_custom_api_base)
        row.api_base = validate_deepseek_api_base(
            api_base,
            allow_custom=allow_custom,
            allow_acceptance_mock=ai_settings.acceptance_mock_http_allowed,
        )
        for field in _WRITABLE_CONFIG_FIELDS:
            if field not in values:
                continue
            value = _sanitize_metadata(values[field]) if field == "config" else values[field]
            setattr(row, field, value)
        api_key = _plaintext_api_key_update(values.get("api_key"))
        if api_key is not None:
            encryptor = _field_encryptor()
            row.api_key_ciphertext = encryptor.encrypt(api_key)
            row.api_key_fingerprint = fingerprint_secret(api_key)
        self.session.flush()
        return row

    def delete_provider_key(self, provider: str = "deepseek") -> AIProviderConfig:
        row = self.get_or_create_provider_config(provider)
        row.api_key_ciphertext = None
        row.api_key_fingerprint = None
        self.session.flush()
        return row

    async def list_models(self, provider: str = "deepseek") -> list[dict[str, Any]]:
        row = self.get_or_create_provider_config(provider)
        runtime = self._runtime_config(row, require_model=False)
        ai_provider = self.provider_registry.create(runtime, client=self.http_client)
        models = await ai_provider.list_models()
        return [
            {"id": item.id, "owned_by": item.owned_by, "metadata": item.metadata}
            for item in models
        ]

    async def test_connection(self, provider: str = "deepseek") -> dict[str, Any]:
        row = self.get_or_create_provider_config(provider)
        started = time.perf_counter()
        try:
            models = await self.list_models(provider)
        except Exception as exc:
            row.last_tested_at = utc_now()
            row.last_test_status = "failed"
            row.last_error_sanitized = sanitize_error(exc)
            self.session.flush()
            raise
        row.last_tested_at = utc_now()
        row.last_test_status = "success"
        row.last_error_sanitized = None
        self.session.flush()
        return {
            "status": "success",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model_count": len(models),
        }

    async def summarize_event(
        self,
        event_id: int,
        *,
        force: bool = False,
        auto: bool = False,
        job_type: str = "summarize_event",
        run: AIRun | None = None,
        worker_name: str | None = None,
    ) -> EventAIInsight:
        started_perf = time.perf_counter()
        started_at = utc_now()
        row = self.get_or_create_provider_config("deepseek")
        if not row.enabled:
            raise AIConfigurationError("AI is disabled")
        if auto and not row.auto_process_enabled:
            raise AIConfigurationError("AI auto processing is disabled")
        event = self._event_or_none(event_id)
        if event is None:
            raise AIConfigurationError("event not found")
        if auto and not auto_event_allowed(event, row):
            raise AIConfigurationError("event severity and priority are below auto minimum")

        template = self.ensure_default_prompt_template()
        event_input = build_event_input(event)
        input_hash = compute_input_hash(event_input.model_dump(mode="json"), template.version)
        runtime = self._runtime_config(row, require_model=True)
        existing = self._find_existing(event.id, runtime, template.version, input_hash)
        if existing is not None and not force:
            if run is not None:
                _finish_ai_run(
                    run,
                    event_id=event.id,
                    runtime_model=runtime.model,
                    runtime_provider=row.provider,
                    started_at=started_at,
                    started_perf=started_perf,
                    status=_success_status_for_run(run),
                    worker_name=worker_name,
                )
            return existing

        dedupe_key = _input_hash_lock_key(
            row.provider,
            runtime.model,
            event.id,
            template.version,
            input_hash,
        )
        with _limit_controller.input_hash_lock(
            dedupe_key,
        ):
            existing = self._find_existing(event.id, runtime, template.version, input_hash)
            if existing is not None and not force:
                if run is not None:
                    _finish_ai_run(
                        run,
                        event_id=event.id,
                        runtime_model=runtime.model,
                        runtime_provider=row.provider,
                        started_at=started_at,
                        started_perf=started_perf,
                        status=_success_status_for_run(run),
                        worker_name=worker_name,
                    )
                return existing

            usage = self._check_budget(row, auto=auto)
            _limit_controller.sync_daily_usage(row.provider, usage)
            if run is None:
                run = AIRun(
                    job_type=job_type,
                    provider=row.provider,
                    model=runtime.model,
                    event_count=1,
                    event_ids=[event.id],
                    status="started",
                    queued_at=started_at,
                    started_at=started_at,
                    queue_wait_ms=0,
                    worker_name=worker_name,
                )
                self.session.add(run)
            else:
                if not _claim_run_started(
                    self.session,
                    run,
                    event_id=event.id,
                    runtime_model=runtime.model,
                    runtime_provider=row.provider,
                    started_at=started_at,
                    worker_name=worker_name,
                ):
                    _raise_if_run_stopped(run)
            self.session.flush()
            try:
                with _limit_controller.reserve(
                    row.provider,
                    max_concurrency=row.max_concurrency,
                    requests_per_minute=int((row.config or {}).get("requests_per_minute", 60)),
                    daily_request_budget=row.daily_request_budget,
                    daily_token_budget=row.daily_token_budget,
                ):
                    provider = self.provider_registry.create(runtime, client=self.http_client)
                    (
                        output,
                        prompt_tokens,
                        completion_tokens,
                        retries,
                        provider_latency_ms,
                    ) = await call_with_repair(
                        provider,
                        event_input,
                        template,
                    )
                self.session.refresh(run)
                if run.status not in ACTIVE_JOB_STATUSES:
                    _raise_if_run_stopped(run)
                insight = self._save_insight(
                    event,
                    runtime=runtime,
                    prompt_version=template.version,
                    input_hash=input_hash,
                    output=normalize_output_sources(output, event_input),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    input_quality=event_input.input_quality,
                )
                run_status = _success_status_for_run(run)
                if not _claim_run_completion(
                    self.session,
                    run,
                    status=run_status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    retry_count=retries,
                    provider_latency_ms=provider_latency_ms,
                    started_perf=started_perf,
                ):
                    _raise_if_run_stopped(run)
                _limit_controller.record_token_usage(
                    row.provider,
                    prompt_tokens + completion_tokens,
                )
                _limit_controller.record_success(row.provider)
                self.session.flush()
                return insight
            except AIJobCancelledError:
                raise
            except AIJobStoppedError:
                raise
            except Exception as exc:
                if not _claim_run_failure(self.session, run, exc, started_perf=started_perf):
                    _raise_if_run_stopped(run)
                if isinstance(exc, AITransientError):
                    _limit_controller.record_failure(row.provider)
                self.session.flush()
                raise

    async def summarize_event_batch(
        self,
        event_ids: list[int],
        *,
        force: bool = False,
        auto: bool = False,
    ) -> list[dict[str, Any]]:
        results = []
        for event_id in event_ids:
            try:
                insight = await self.summarize_event(
                    event_id,
                    force=force,
                    auto=auto,
                    job_type="summarize_event_batch",
                )
                results.append(
                    {"event_id": event_id, "status": "success", "insight_id": insight.id}
                )
            except Exception as exc:
                results.append(
                    {"event_id": event_id, "status": "failed", "error": sanitize_error(exc)}
                )
        return results

    def latest_insight(self, event_id: int) -> EventAIInsight | None:
        return self.session.scalar(
            select(EventAIInsight)
            .where(EventAIInsight.event_id == event_id)
            .order_by(EventAIInsight.generated_at.desc().nullslast(), EventAIInsight.id.desc())
        )

    def mark_stale_runs(self, *, limit: int = 200) -> int:
        return mark_stale_ai_runs(self.session, limit=limit)

    def ensure_default_prompt_template(self) -> AIPromptTemplate:
        row = self.session.scalar(
            select(AIPromptTemplate).where(
                AIPromptTemplate.key == DEFAULT_PROMPT_KEY,
                AIPromptTemplate.version == DEFAULT_PROMPT_VERSION,
            )
        )
        if row is not None:
            return row
        row = AIPromptTemplate(
            key=DEFAULT_PROMPT_KEY,
            name="Web3 事件中文整理",
            system_prompt=SYSTEM_PROMPT,
            user_prompt_template=USER_PROMPT_TEMPLATE,
            output_schema_version=DEFAULT_OUTPUT_SCHEMA_VERSION,
            enabled=True,
            version=DEFAULT_PROMPT_VERSION,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _runtime_config(
        self,
        row: AIProviderConfig,
        *,
        require_model: bool,
    ) -> AIProviderRuntimeConfig:
        if not row.api_key_ciphertext:
            raise AIConfigurationError("DeepSeek API Key is not configured")
        if require_model and not row.model:
            raise AIConfigurationError("DeepSeek model is not configured")
        return AIProviderRuntimeConfig(
            provider=row.provider,
            api_base=row.api_base.rstrip("/"),
            api_key=_field_encryptor().decrypt(row.api_key_ciphertext),
            model=row.model or "",
            timeout_seconds=row.timeout_seconds,
            max_tokens=row.max_tokens,
            temperature=row.temperature,
            thinking_enabled=row.thinking_enabled,
        )

    def _find_existing(
        self,
        event_id: int,
        runtime: AIProviderRuntimeConfig,
        prompt_version: str,
        input_hash: str,
    ) -> EventAIInsight | None:
        return self.session.scalar(
            select(EventAIInsight).where(
                EventAIInsight.event_id == event_id,
                EventAIInsight.provider == runtime.provider,
                EventAIInsight.model == runtime.model,
                EventAIInsight.prompt_version == prompt_version,
                EventAIInsight.input_hash == input_hash,
                EventAIInsight.status == "success",
            )
        )

    def _save_insight(
        self,
        event: Event,
        *,
        runtime: AIProviderRuntimeConfig,
        prompt_version: str,
        input_hash: str,
        output: AIInsightOutput,
        prompt_tokens: int,
        completion_tokens: int,
        input_quality: str,
    ) -> EventAIInsight:
        row = self._find_existing(event.id, runtime, prompt_version, input_hash)
        if row is None:
            row = EventAIInsight(
                event_id=event.id,
                provider=runtime.provider,
                model=runtime.model,
                prompt_version=prompt_version,
                input_hash=input_hash,
            )
            self.session.add(row)
        apply_output(row, output)
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.input_quality = input_quality
        row.generated_at = utc_now()
        row.status = "success"
        row.error_sanitized = None
        self.session.flush()
        return row

    def _check_budget(self, row: AIProviderConfig, *, auto: bool) -> AIUsageSnapshot:
        usage = self.usage_today(row.provider)
        if auto and row.daily_token_budget == 0:
            raise AIBudgetExceededError("AI auto processing token budget is zero")
        if row.daily_token_budget > 0 and usage.tokens_today >= row.daily_token_budget:
            raise AIBudgetExceededError("AI daily token budget exceeded")
        if row.daily_request_budget > 0 and usage.requests_today >= row.daily_request_budget:
            raise AIBudgetExceededError("AI daily request budget exceeded")
        return usage

    def _event_or_none(self, event_id: int) -> Event | None:
        return self.session.scalar(
            select(Event)
            .options(
                selectinload(Event.sources).selectinload(EventSource.source),
                selectinload(Event.sources).selectinload(EventSource.raw_document),
            )
            .where(Event.id == event_id)
        )


async def call_with_repair(
    provider: AIProvider,
    event_input: AIEventInput,
    template: AIPromptTemplate,
) -> tuple[AIInsightOutput, int, int, int, int]:
    first = await provider.chat_completion(build_messages(event_input, template))
    try:
        output = validate_ai_output(first.content)
        return output, first.prompt_tokens, first.completion_tokens, 0, first.latency_ms or 0
    except Exception:
        retry = await provider.chat_completion(
            build_repair_messages(event_input, template, first.content)
        )
        output = validate_ai_output(retry.content)
        return (
            output,
            first.prompt_tokens + retry.prompt_tokens,
            first.completion_tokens + retry.completion_tokens,
            1,
            (first.latency_ms or 0) + (retry.latency_ms or 0),
        )


def build_messages(event_input: AIEventInput, template: AIPromptTemplate) -> list[AIMessage]:
    payload_json = _json_dumps(event_input.model_dump(mode="json"))
    return [
        AIMessage(role="system", content=template.system_prompt),
        AIMessage(
            role="user",
            content=template.user_prompt_template.format(event_payload_json=payload_json),
        ),
    ]


def build_repair_messages(
    event_input: AIEventInput,
    template: AIPromptTemplate,
    invalid_output: str,
) -> list[AIMessage]:
    payload_json = _json_dumps(event_input.model_dump(mode="json"))
    return [
        AIMessage(role="system", content=template.system_prompt),
        AIMessage(
            role="user",
            content=REPAIR_PROMPT_TEMPLATE.format(
                event_payload_json=payload_json,
                invalid_output=invalid_output[:4000],
            ),
        ),
    ]


def validate_ai_output(content: str) -> AIInsightOutput:
    try:
        return AIInsightOutput.model_validate(_coerce_ai_output_payload(parse_json_object(content)))
    except Exception as exc:
        raise AIJSONValidationError("AI JSON output validation failed") from exc


def _coerce_ai_output_payload(payload: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(payload)
    for field, text_key in (
        ("key_facts", "text"),
        ("entities", "name"),
        ("facts", "text"),
        ("inferences", "text"),
    ):
        if field in coerced:
            coerced[field] = _coerce_object_list(coerced[field], text_key=text_key)
    for field in ("symbols", "chains", "source_event_ids", "source_urls"):
        if field in coerced:
            coerced[field] = _coerce_string_list(coerced[field])
    return coerced


def _coerce_object_list(value: Any, *, text_key: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        text = str(item).strip()
        if text:
            normalized.append({text_key: text})
    return normalized


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [text for item in items if (text := str(item).strip())]


def normalize_output_sources(
    output: AIInsightOutput,
    event_input: AIEventInput,
) -> AIInsightOutput:
    allowed_ids = {str(event_input.event_id)}
    allowed_urls = set(event_input.original_urls)
    output.source_event_ids = [item for item in output.source_event_ids if item in allowed_ids]
    if not output.source_event_ids:
        output.source_event_ids = [str(event_input.event_id)]
    output.source_urls = [item for item in output.source_urls if item in allowed_urls]
    if not output.source_urls and event_input.original_urls:
        output.source_urls = [event_input.original_urls[0]]
    for fact in output.facts:
        if str(fact.get("source_event_id")) not in allowed_ids:
            fact["source_event_id"] = str(event_input.event_id)
        if event_input.original_urls:
            if fact.get("source_url") not in allowed_urls:
                fact["source_url"] = event_input.original_urls[0]
        else:
            fact.pop("source_url", None)
    for inference in output.inferences:
        inference.setdefault("type", "inference")
    return output


def build_event_input(event: Event) -> AIEventInput:
    return build_ai_event_input(event)


def mark_stale_ai_runs(session: Session, *, limit: int = 200) -> int:
    stmt = (
        select(AIRun)
        .where(AIRun.status.in_(["queued", "started", "retrying"]))
        .order_by(AIRun.queued_at.asc().nullslast(), AIRun.created_at.asc())
        .limit(limit)
    )
    changed = 0
    for run in session.scalars(stmt):
        if mark_stale_ai_run(run):
            changed += 1
    if changed:
        session.flush()
    return changed


def mark_stale_ai_run(run: AIRun) -> bool:
    if run.status not in {"queued", "started", "retrying"}:
        return False
    runtime_settings = get_ai_runtime_settings()
    now = utc_now()
    queued_at = ensure_utc(run.queued_at or run.created_at)
    started_at = ensure_utc(run.started_at)
    if started_at is None and queued_at is not None:
        age_seconds = (now - queued_at).total_seconds()
        if age_seconds >= runtime_settings.job_timeout_seconds:
            return _claim_run_stale_failed(
                run,
                "ai_job_timeout",
                "AI 任务排队超时，请检查 Worker 状态后重试",
                expected_status=run.status,
                require_unstarted=True,
            )
        if age_seconds >= runtime_settings.job_stuck_seconds:
            return _claim_run_stale_failed(
                run,
                "ai_job_stuck",
                "AI 任务长时间未被 Worker 消费，请检查 Celery Worker",
                expected_status=run.status,
                require_unstarted=True,
            )
    if started_at is not None:
        age_seconds = (now - started_at).total_seconds()
        if age_seconds >= runtime_settings.job_timeout_seconds:
            return _claim_run_stale_failed(
                run,
                "ai_job_timeout",
                "AI 任务执行超时，请检查 Worker 或模型服务",
                expected_status=run.status,
                expected_started_at=started_at,
            )
    return False


def _claim_run_stale_failed(
    run: AIRun,
    code: str,
    message: str,
    *,
    expected_status: str,
    require_unstarted: bool = False,
    expected_started_at: datetime | None = None,
) -> bool:
    session = object_session(run)
    if session is None:
        return False
    finished_at = utc_now()
    started = ensure_utc(run.queued_at or run.started_at or run.created_at)
    values: dict[str, Any] = {
        "status": "failed",
        "error_code": code,
        "error_sanitized": message,
        "error_message_sanitized": message,
        "finished_at": finished_at,
    }
    if started:
        total_latency_ms = max(0, int((finished_at - started).total_seconds() * 1000))
        values["total_latency_ms"] = total_latency_ms
        values["latency_ms"] = total_latency_ms
    result = session.execute(
        update(AIRun)
        .where(
            *_stale_run_filters(
                run,
                expected_status=expected_status,
                require_unstarted=require_unstarted,
                expected_started_at=expected_started_at,
            )
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.refresh(run)
    return result.rowcount == 1


def compute_input_hash(payload: dict[str, Any], prompt_version: str) -> str:
    return hashlib.sha256(
        _json_dumps({"prompt_version": prompt_version, "payload": payload}).encode("utf-8")
    ).hexdigest()


def provider_config_to_public_dict(
    row: AIProviderConfig,
    usage: AIUsageSnapshot,
) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "enabled": row.enabled,
        "api_base": row.api_base,
        "api_key_configured": bool(row.api_key_ciphertext),
        "api_key_masked": mask_fingerprint(row.api_key_fingerprint),
        "api_key_fingerprint": _public_key_fingerprint(row.api_key_fingerprint),
        "model": row.model,
        "timeout_seconds": row.timeout_seconds,
        "max_concurrency": row.max_concurrency,
        "max_tokens": row.max_tokens,
        "temperature": row.temperature,
        "thinking_enabled": row.thinking_enabled,
        "daily_token_budget": row.daily_token_budget,
        "daily_request_budget": row.daily_request_budget,
        "auto_process_enabled": row.auto_process_enabled,
        "auto_minimum_severity": row.auto_minimum_severity,
        "config": row.config or {},
        "last_tested_at": row.last_tested_at,
        "last_test_status": row.last_test_status,
        "last_error_sanitized": row.last_error_sanitized,
        "tokens_today": usage.tokens_today,
        "requests_today": usage.requests_today,
        "failures_today": usage.failures_today,
        "updated_at": row.updated_at,
    }


def sanitize_error(exc: BaseException) -> str:
    code = getattr(exc, "error_code", exc.__class__.__name__)
    message = str(exc)[:200]
    sanitized = f"{code}: {message}" if message else str(code)
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", sanitized)
    sanitized = re.sub(r"sk-[A-Za-z0-9._-]+", "sk-[redacted]", sanitized)
    sanitized = re.sub(r"(?i)(api[_-]?key=)[^&\s]+", r"\1[redacted]", sanitized)
    return sanitized


def apply_output(row: EventAIInsight, output: AIInsightOutput) -> None:
    row.summary_zh = output.summary_zh
    row.headline_zh = output.headline_zh
    row.key_facts = output.key_facts
    row.entities = output.entities
    row.symbols = output.symbols
    row.chains = output.chains
    row.event_type = output.event_type
    row.importance_score = output.importance_score
    row.risk_level = output.risk_level
    row.sentiment = output.sentiment
    row.market_impact = output.market_impact
    row.facts = output.facts
    row.inferences = output.inferences
    row.confidence = output.confidence
    row.source_event_ids = output.source_event_ids
    row.source_urls = output.source_urls


def _field_encryptor() -> FieldEncryptor:
    if not settings.field_encryption_key:
        raise AIConfigurationError(MISSING_FIELD_ENCRYPTION_KEY_MESSAGE)
    try:
        return FieldEncryptor(settings.field_encryption_key)
    except FieldEncryptionError as exc:
        raise AIConfigurationError(INVALID_FIELD_ENCRYPTION_KEY_MESSAGE) from exc


def _plaintext_api_key_update(value: Any) -> str | None:
    if value is None:
        return None
    plaintext = str(value).strip()
    if not plaintext:
        return None
    if plaintext.startswith(MASKED_KEY_PREFIXES[0]) or plaintext.lower().startswith(
        MASKED_KEY_PREFIXES[1]
    ):
        return None
    return plaintext


def _public_key_fingerprint(fingerprint: str | None) -> str | None:
    if not fingerprint:
        return None
    return f"sha256:{fingerprint[:16]}..."


def _sanitize_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30:
                result["truncated"] = True
                break
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                result[key] = "[redacted]"
            else:
                result[key] = _sanitize_metadata(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_metadata(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:200]


def _truncate(value: str, limit: int = 1500) -> str:
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def auto_event_allowed(event: Event, row: AIProviderConfig) -> bool:
    if _severity_allowed(event.severity, row.auto_minimum_severity):
        return True
    minimum_priority = _auto_minimum_priority_score(row)
    if minimum_priority <= 0:
        return False
    return event_priority_score(event) >= minimum_priority


def _auto_minimum_priority_score(row: AIProviderConfig) -> int:
    config = row.config or {}
    value = config.get("auto_minimum_priority_score", 85)
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 85


def _severity_allowed(value: str, minimum: str) -> bool:
    rank = {"low": 0, "normal": 1, "medium": 1, "high": 2, "critical": 3}
    return rank.get(value, 0) >= rank.get(minimum, 2)


def _input_hash_lock_key(
    provider: str,
    model: str,
    event_id: int,
    prompt_version: str,
    input_hash: str,
) -> str:
    return f"{provider}:{model}:{event_id}:{prompt_version}:{input_hash}"


def _finish_ai_run(
    run: AIRun,
    *,
    event_id: int,
    runtime_model: str,
    runtime_provider: str,
    started_at: datetime,
    started_perf: float,
    status: str,
    worker_name: str | None,
) -> None:
    session = object_session(run)
    values: dict[str, Any] = {
        "status": status,
        "started_at": run.started_at or started_at,
        "worker_name": worker_name or run.worker_name,
        "model": runtime_model,
        "provider": runtime_provider,
        "event_count": max(run.event_count or 0, 1),
        "event_ids": run.event_ids or [event_id],
        "total_latency_ms": int((time.perf_counter() - started_perf) * 1000),
        "latency_ms": int((time.perf_counter() - started_perf) * 1000),
    }
    queued_at = ensure_utc(run.queued_at)
    if queued_at:
        values["queue_wait_ms"] = max(0, int((started_at - queued_at).total_seconds() * 1000))
    if status == "succeeded":
        values["finished_at"] = utc_now()
    if session is None:
        for key, value in values.items():
            setattr(run, key, value)
        return
    result = session.execute(
        update(AIRun)
        .where(*_active_run_filters(run))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.refresh(run)
        _raise_if_run_stopped(run)
    session.refresh(run)


def _claim_run_started(
    session: Session,
    run: AIRun,
    *,
    event_id: int,
    runtime_model: str,
    runtime_provider: str,
    started_at: datetime,
    worker_name: str | None,
) -> bool:
    values: dict[str, Any] = {
        "status": "started",
        "started_at": run.started_at or started_at,
        "worker_name": worker_name or run.worker_name,
        "model": runtime_model,
        "provider": runtime_provider,
        "event_count": max(run.event_count or 0, 1),
        "event_ids": run.event_ids or [event_id],
    }
    queued_at = ensure_utc(run.queued_at)
    if queued_at:
        values["queue_wait_ms"] = max(0, int((started_at - queued_at).total_seconds() * 1000))
    result = session.execute(
        update(AIRun)
        .where(*_active_run_filters(run))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.refresh(run)
    return result.rowcount == 1


def _success_status_for_run(run: AIRun) -> str:
    return "started" if run.job_type == "summarize_event_batch" else "succeeded"


def _claim_run_completion(
    session: Session,
    run: AIRun,
    *,
    status: str,
    prompt_tokens: int,
    completion_tokens: int,
    retry_count: int,
    provider_latency_ms: int,
    started_perf: float,
) -> bool:
    now = utc_now()
    total_latency_ms = int((time.perf_counter() - started_perf) * 1000)
    values: dict[str, Any] = {
        "status": status,
        "prompt_tokens": (run.prompt_tokens or 0) + prompt_tokens,
        "completion_tokens": (run.completion_tokens or 0) + completion_tokens,
        "retry_count": max(run.retry_count or 0, retry_count),
        "provider_latency_ms": (run.provider_latency_ms or 0) + provider_latency_ms,
        "total_latency_ms": total_latency_ms,
        "latency_ms": total_latency_ms,
    }
    if status == "succeeded":
        values["finished_at"] = now
    result = session.execute(
        update(AIRun)
        .where(*_active_run_filters(run))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.refresh(run)
        return False
    session.refresh(run)
    return True


def _claim_run_failure(
    session: Session,
    run: AIRun,
    exc: BaseException,
    *,
    started_perf: float,
) -> bool:
    total_latency_ms = int((time.perf_counter() - started_perf) * 1000)
    error_message = sanitize_error(exc)
    result = session.execute(
        update(AIRun)
        .where(*_active_run_filters(run))
        .values(
            status="failed",
            error_code=getattr(exc, "error_code", "ai_task_failed"),
            error_sanitized=error_message,
            error_message_sanitized=error_message,
            finished_at=utc_now(),
            total_latency_ms=total_latency_ms,
            latency_ms=total_latency_ms,
        )
        .execution_options(synchronize_session=False)
    )
    session.refresh(run)
    return result.rowcount == 1


def _active_run_filters(run: AIRun) -> tuple[Any, ...]:
    filters: list[Any] = [AIRun.id == run.id, AIRun.status.in_(ACTIVE_JOB_STATUSES)]
    if run.task_id:
        filters.append(AIRun.task_id == run.task_id)
    return tuple(filters)


def _stale_run_filters(
    run: AIRun,
    *,
    expected_status: str,
    require_unstarted: bool,
    expected_started_at: datetime | None,
) -> tuple[Any, ...]:
    filters: list[Any] = [AIRun.id == run.id, AIRun.status == expected_status]
    if run.task_id:
        filters.append(AIRun.task_id == run.task_id)
    if require_unstarted:
        filters.append(AIRun.started_at.is_(None))
    if expected_started_at is not None:
        filters.append(AIRun.started_at == expected_started_at)
    return tuple(filters)


def _raise_if_run_stopped(run: AIRun) -> None:
    if run.status == "cancelled":
        raise AIJobCancelledError("AI 任务已取消")
    if run.status == "failed":
        raise AIJobStoppedError("AI 任务已结束，跳过写入结果")
    raise AIJobStoppedError("AI 任务状态已变化，跳过写入结果")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_WRITABLE_CONFIG_FIELDS = {
    "enabled",
    "model",
    "timeout_seconds",
    "max_concurrency",
    "max_tokens",
    "temperature",
    "thinking_enabled",
    "daily_token_budget",
    "daily_request_budget",
    "auto_process_enabled",
    "auto_minimum_severity",
    "config",
}
_limit_controller = ai_limit_controller


def summarize_event_sync(
    session: Session,
    event_id: int,
    *,
    force: bool = False,
    auto: bool = False,
    timeout_seconds: int | None = None,
    run: AIRun | None = None,
    worker_name: str | None = None,
    job_type: str = "summarize_event",
) -> EventAIInsight:
    import asyncio

    async def _run() -> EventAIInsight:
        coroutine = AIService(session).summarize_event(
            event_id,
            force=force,
            auto=auto,
            run=run,
            worker_name=worker_name,
            job_type=job_type,
        )
        if timeout_seconds is None:
            return await coroutine
        return await asyncio.wait_for(coroutine, timeout=timeout_seconds)

    try:
        return asyncio.run(_run())
    except TimeoutError as exc:
        raise AIRuntimeTimeoutError("AI 同步生成超时") from exc
