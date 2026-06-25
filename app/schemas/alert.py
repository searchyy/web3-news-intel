from __future__ import annotations

from pydantic import BaseModel


class ScoreResult(BaseModel):
    trust_score: int
    status: str
    severity: str
    confirmation_count: int
    reasons: list[str]
    priority_score: int = 0
    priority_tier: str = "noise"
    noise_reasons: list[str] = []


class AlertDecision(BaseModel):
    should_alert: bool
    requires_review: bool = False
    reason: str
    severity: str