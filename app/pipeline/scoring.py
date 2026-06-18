from __future__ import annotations

from app.db.models import Event, EventSource
from app.pipeline.category import all_media_source_types, is_official_source, is_sensitive_category
from app.pipeline.severity import severity_for_category
from app.schemas.alert import ScoreResult

SOURCE_BASE_SCORE = {
    "regulator_official": 100,
    "exchange_official": 95,
    "protocol_official": 95,
    "governance_api": 90,
    "onchain_data": 85,
    "security_alert": 85,
    "tier1_media": 75,
    "chinese_media": 70,
    "aggregator": 50,
    "social": 40,
}


class ScoringService:
    def score(self, event: Event, event_sources: list[EventSource]) -> ScoreResult:
        source_types = [
            event_source.source.source_type
            for event_source in event_sources
            if getattr(event_source, "source", None) is not None
        ]
        source_scores = [event_source.source_score for event_source in event_sources]
        max_source_score = max(source_scores, default=event.trust_score)
        independent_sources = len({event_source.source_id for event_source in event_sources})
        bonus = min(15, max(0, independent_sources - 1) * 8)
        trust_score = min(100, max_source_score + bonus)
        severity = severity_for_category(event.category)
        reasons: list[str] = []

        has_official = any(
            is_official_source(source_type) and score >= 90
            for source_type, score in zip(source_types, source_scores, strict=False)
        )
        if has_official:
            status = "confirmed"
            reasons.append("official source")
        elif is_sensitive_category(event.category) and all_media_source_types(source_types):
            status = (
                "confirmed" if independent_sources >= 2 and trust_score >= 80 else "needs_review"
            )
            reasons.append("sensitive media-only event")
        elif independent_sources >= 2 and trust_score >= 80:
            status = "confirmed"
            reasons.append("cross-source confirmation")
        else:
            status = "needs_review"
            reasons.append("single non-official source")

        if any(source_type == "onchain_data" for source_type in source_types):
            reasons.append("on-chain signal is labeled as inference")

        return ScoreResult(
            trust_score=trust_score,
            status=status,
            severity=severity,
            confirmation_count=max(1, independent_sources),
            reasons=reasons,
        )


def source_base_score(source_type: str) -> int:
    return SOURCE_BASE_SCORE.get(source_type, 50)
