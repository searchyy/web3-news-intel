from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Event


@dataclass(slots=True)
class DigestBatch:
    events: list[Event]
    remaining_count: int


def build_digest(events: list[Event], *, limit: int = 10) -> DigestBatch:
    ranked = sorted(
        events,
        key=lambda event: (event.severity == "critical", event.trust_score),
        reverse=True,
    )
    return DigestBatch(events=ranked[:limit], remaining_count=max(0, len(ranked) - limit))
