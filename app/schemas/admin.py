from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.field_encryption import mask_fingerprint


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminAuthResponse(BaseModel):
    authenticated: bool
    username: str
    csrf_token: str | None = None


class DashboardSummary(BaseModel):
    events_last_hour: int
    events_last_24h: int
    critical_high_count: int
    enabled_sources: int
    failed_sources: int
    successful_deliveries: int
    failed_deliveries: int
    pending_feishu_groups: int


class TimeSeriesPoint(BaseModel):
    timestamp: datetime
    count: int


class BreakdownPoint(BaseModel):
    key: str
    count: int


class DestinationCreate(BaseModel):
    key: str
    name: str
    provider: str = Field(pattern="^(feishu_app|feishu_webhook|telegram|discord|generic_webhook)$")
    chat_id: str | None = None
    chat_name: str | None = None
    webhook_url: str | None = None
    enabled: bool = False
    config: dict = Field(default_factory=dict)


class DestinationPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    status: str | None = Field(default=None, pattern="^(pending|active|degraded|disabled)$")
    webhook_url: str | None = None
    config: dict | None = None


class DestinationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    provider: str
    enabled: bool
    status: str
    chat_id: str | None
    chat_name: str | None
    secret_fingerprint: str | None
    config: dict
    activated_at: datetime | None
    last_tested_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("secret_fingerprint")
    @classmethod
    def mask_secret_fingerprint(cls, value: str | None) -> str | None:
        return mask_fingerprint(value)

    @field_validator("config")
    @classmethod
    def redact_secret_config(cls, value: dict) -> dict:
        return _redact_config(value)


class RuleBase(BaseModel):
    name: str
    enabled: bool = False
    minimum_severity: str = Field(default="normal", pattern="^(low|normal|high|critical)$")
    categories: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    delivery_mode: str = Field(default="immediate", pattern="^(immediate|digest)$")
    digest_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    quiet_hours_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: str = "UTC"
    maximum_messages_per_hour: int = Field(default=30, ge=1, le=500)
    critical_bypass_quiet_hours: bool = False

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return value


class RuleCreate(RuleBase):
    destination_id: uuid.UUID


class RulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    minimum_severity: str | None = Field(default=None, pattern="^(low|normal|high|critical)$")
    categories: list[str] | None = None
    sources: list[str] | None = None
    symbols: list[str] | None = None
    chains: list[str] | None = None
    delivery_mode: str | None = Field(default=None, pattern="^(immediate|digest)$")
    digest_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    quiet_hours_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: str | None = None
    maximum_messages_per_hour: int | None = Field(default=None, ge=1, le=500)
    critical_bypass_quiet_hours: bool | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return value


class RuleRead(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    destination_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    destination_id: uuid.UUID | None
    channel: str
    target: str
    status: str
    delivery_variant: str
    provider_message_id: str | None
    response_status: int | None
    attempts: int
    last_error: str | None
    delivered_at: datetime | None
    acknowledged_at: datetime | None
    created_at: datetime


class DeliveryPage(BaseModel):
    items: list[DeliveryRead]
    total: int
    page: int
    page_size: int


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admin_subject: str
    action: str
    resource_type: str
    resource_id: str | None
    metadata_: dict
    request_id: str
    ip_hash: str | None
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int


class FeishuConfigBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    feishu_app_id: str | None = Field(default=None, alias="FEISHU_APP_ID")
    feishu_app_secret: str | None = Field(default=None, alias="FEISHU_APP_SECRET")
    feishu_verification_token: str | None = Field(
        default=None, alias="FEISHU_VERIFICATION_TOKEN"
    )
    feishu_encrypt_key: str | None = Field(default=None, alias="FEISHU_ENCRYPT_KEY")
    feishu_test_chat_id: str | None = Field(default=None, alias="FEISHU_TEST_CHAT_ID")
    feishu_enabled: bool = Field(default=False, alias="FEISHU_ENABLED")
    feishu_send_enabled: bool = Field(default=False, alias="FEISHU_SEND_ENABLED")


class FeishuConfigRead(FeishuConfigBase):
    connection_status: Literal["not_tested", "connected", "failed"] = "not_tested"


class FeishuConfigWrite(FeishuConfigBase):
    pass


class FeishuTestResult(BaseModel):
    status: Literal["success", "failed"]
    latency_ms: int | None = None
    message: str | None = None
    error: str | None = None


def _redact_config(value: dict) -> dict:
    blocked = ("secret", "token", "password", "webhook", "authorization", "cookie", "url")
    redacted = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in blocked):
            redacted[key] = "[redacted]"
        elif isinstance(item, dict):
            redacted[key] = _redact_config(item)
        elif isinstance(item, list):
            redacted[key] = [
                _redact_config(entry) if isinstance(entry, dict) else entry for entry in item
            ]
        else:
            redacted[key] = item
    return redacted
