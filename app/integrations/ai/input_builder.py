from __future__ import annotations

from app.db.models import Event
from app.integrations.ai.evidence_builder import build_evidence_pack
from app.integrations.ai.schemas import AIEventExcerpt, AIEventInput


def build_event_input(event: Event) -> AIEventInput:
    return AIInputBuilder().build(event)


class AIInputBuilder:
    def build(self, event: Event) -> AIEventInput:
        evidence = build_evidence_pack(event)
        source_urls = evidence.source_urls
        return AIEventInput(
            event_id=event.id,
            title=evidence.title,
            summary=evidence.summary,
            source_names=evidence.source_names,
            published_at=event.published_at.isoformat() if event.published_at else None,
            original_urls=source_urls,
            source_urls=source_urls,
            category=event.category,
            severity=event.severity,
            symbols=list(event.symbols or [])[:20],
            chains=list(event.chains or [])[:20],
            excerpts=[
                AIEventExcerpt(
                    source_name=excerpt.source_name,
                    source_url=excerpt.source_url,
                    text=excerpt.text,
                )
                for excerpt in evidence.excerpts
            ],
            input_quality=evidence.input_quality,
            metadata=evidence.metadata,
        )
