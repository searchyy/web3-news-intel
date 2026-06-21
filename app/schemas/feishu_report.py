from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReportType = Literal[
    "immediate",
    "digest_15m",
    "digest_30m",
    "hourly",
    "daily_morning",
    "daily_evening",
    "custom",
]


class ReportScheduleBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    report_type: ReportType = "hourly"
    timezone: str = "Asia/Taipei"
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    saved_search_id: int | None = Field(default=None, ge=1)
    source_groups: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    minimum_trust_score: int | None = Field(default=None, ge=0, le=100)
    include_ai_summary: bool = True
    maximum_events: int = Field(default=20, ge=1, le=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("未知时区") from exc
        return value

    @field_validator("source_groups", "categories", "severities", "symbols", "chains")
    @classmethod
    def normalize_list(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = " ".join(str(item).strip().split())
            if not cleaned:
                continue
            key = cleaned.upper()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned.upper() if key == cleaned.upper() else cleaned)
        return result

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> ReportScheduleBase:
        if self.report_type == "digest_15m":
            self.interval_minutes = self.interval_minutes or 15
        elif self.report_type == "digest_30m":
            self.interval_minutes = self.interval_minutes or 30
        elif self.report_type == "hourly":
            self.interval_minutes = self.interval_minutes or 60
        elif self.report_type in {"daily_morning", "daily_evening", "custom"}:
            if self.hour is None:
                self.hour = 9 if self.report_type == "daily_morning" else 18
            if self.minute is None:
                self.minute = 0
        elif self.report_type == "immediate":
            self.interval_minutes = self.interval_minutes or 5
        return self


class ReportScheduleCreate(ReportScheduleBase):
    destination_id: uuid.UUID


class ReportSchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    report_type: ReportType | None = None
    timezone: str | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    saved_search_id: int | None = Field(default=None, ge=1)
    source_groups: list[str] | None = None
    categories: list[str] | None = None
    severities: list[str] | None = None
    symbols: list[str] | None = None
    chains: list[str] | None = None
    minimum_trust_score: int | None = Field(default=None, ge=0, le=100)
    include_ai_summary: bool | None = None
    maximum_events: int | None = Field(default=None, ge=1, le=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("未知时区") from exc
        return value


class ReportScheduleRead(ReportScheduleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    destination_id: uuid.UUID
    activated_at: datetime | None
    last_window_start: datetime | None
    last_window_end: datetime | None
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_result: str | None
    last_error_sanitized: str | None
    created_at: datetime
    updated_at: datetime


class ReportEventPreview(BaseModel):
    id: int
    title: str
    severity: str
    category: str
    published_at: datetime | None = None
    first_seen_at: datetime
    primary_url: str | None = None
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    ai_summary_zh: str | None = None


class ReportPreviewRead(BaseModel):
    schedule_id: int
    destination_id: uuid.UUID
    report_type: str
    window_start: datetime
    window_end: datetime
    event_count: int
    critical_high_count: int
    top_symbols: list[str]
    top_categories: list[str]
    summary_zh: str
    omitted_count: int
    card: dict
    events: list[ReportEventPreview]


class ReportRunResponse(BaseModel):
    schedule_id: int
    queued: bool


class ReportSendResultRead(BaseModel):
    schedule_id: int
    delivery_id: int | None = None
    status: str
    dry_run: bool = False
    message: str | None = None
