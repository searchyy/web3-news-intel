from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RiskLevel = Literal["low", "medium", "high", "critical"]
Sentiment = Literal["negative", "neutral", "positive", "mixed"]


class AIInsightOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline_zh: str
    summary_zh: str
    key_facts: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    event_type: str
    importance_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    sentiment: Sentiment
    market_impact: str
    facts: list[dict[str, Any]] = Field(default_factory=list)
    inferences: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source_event_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("headline_zh", "summary_zh", "event_type", "market_impact")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("symbols", "chains", "source_event_ids", "source_urls")
    @classmethod
    def dedupe_strings(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    @model_validator(mode="after")
    def require_uncertain_when_empty(self) -> Self:
        if not self.facts and self.confidence > 0.3:
            self.confidence = 0.3
        if not self.summary_zh:
            self.summary_zh = "不确定"
        if not self.headline_zh:
            self.headline_zh = "不确定"
        return self


class AIEventInput(BaseModel):
    event_id: int
    title: str
    summary: str | None = None
    source_names: list[str] = Field(default_factory=list)
    published_at: str | None = None
    original_urls: list[str] = Field(default_factory=list)
    category: str
    severity: str
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AIUsageSnapshot(BaseModel):
    tokens_today: int = 0
    requests_today: int = 0
    failures_today: int = 0


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(content[start : end + 1])
        else:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("AI output must be a JSON object")
    return parsed
