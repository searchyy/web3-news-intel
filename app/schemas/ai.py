from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import AIRun, EventAIInsight


class AIProviderConfigRead(BaseModel):
    provider: Literal["deepseek"]
    enabled: bool
    api_base: str
    api_key_configured: bool
    api_key_masked: str | None = None
    api_key_fingerprint: str | None = None
    model: str | None = None
    timeout_seconds: int
    max_concurrency: int
    max_tokens: int
    temperature: float
    thinking_enabled: bool
    daily_token_budget: int
    daily_request_budget: int
    auto_process_enabled: bool
    auto_minimum_severity: str
    config: dict[str, Any] = Field(default_factory=dict)
    last_tested_at: datetime | None = None
    last_test_status: str | None = None
    last_error_sanitized: str | None = None
    tokens_today: int = 0
    requests_today: int = 0
    failures_today: int = 0
    updated_at: datetime | None = None


class AIProviderConfigWrite(BaseModel):
    enabled: bool = False
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: int = Field(default=90, ge=1, le=300)
    max_concurrency: int = Field(default=2, ge=1, le=20)
    max_tokens: int = Field(default=1200, ge=128, le=8192)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    thinking_enabled: bool = False
    daily_token_budget: int = Field(default=0, ge=0)
    daily_request_budget: int = Field(default=0, ge=0)
    auto_process_enabled: bool = False
    auto_minimum_severity: str = Field(default="high", pattern="^(low|normal|high|critical)$")
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("api_key")
    @classmethod
    def strip_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AIModelRead(BaseModel):
    id: str
    owned_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AITestResult(BaseModel):
    status: Literal["success", "failed"]
    latency_ms: int | None = None
    model_count: int | None = None
    error: str | None = None


class AIQueuedTask(BaseModel):
    queued: bool
    task_id: str
    event_id: int | None = None
    event_ids: list[int] = Field(default_factory=list)


class AISummaryRequest(BaseModel):
    force: bool = False


class AIBatchSummaryRequest(BaseModel):
    event_ids: list[int] = Field(min_length=1, max_length=100)
    force: bool = False


class AITaskStatus(BaseModel):
    task_id: str
    status: str
    result: Any = None


class AIRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_type: str
    provider: str
    model: str | None
    event_count: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float | None
    latency_ms: int | None
    status: str
    retry_count: int
    error_code: str | None
    error_sanitized: str | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_model(cls, item: AIRun) -> AIRunRead:
        return cls.model_validate(item)


class AIRunPage(BaseModel):
    items: list[AIRunRead]
    total: int
    page: int
    page_size: int


class EventAIInsightRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    summary_zh: str | None
    headline_zh: str | None
    key_facts: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    symbols: list[str]
    chains: list[str]
    event_type: str | None
    importance_score: int
    risk_level: str
    sentiment: str
    market_impact: str | None
    facts: list[dict[str, Any]]
    inferences: list[dict[str, Any]]
    confidence: float
    source_event_ids: list[str]
    source_urls: list[str]
    prompt_tokens: int
    completion_tokens: int
    generated_at: datetime | None
    status: str
    error_sanitized: str | None

    @classmethod
    def from_model(cls, item: EventAIInsight) -> EventAIInsightRead:
        return cls.model_validate(item)


class DeepSeekProviderRead(AIProviderConfigRead):
    pass


class DeepSeekProviderWrite(AIProviderConfigWrite):
    pass


class AIModelInfo(AIModelRead):
    object: str | None = None


class AIConnectionTestResult(AITestResult):
    models: list[AIModelInfo] = Field(default_factory=list)


EventAiInsightRead = EventAIInsightRead
