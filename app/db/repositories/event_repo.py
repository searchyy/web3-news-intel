from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event


class EventRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(
        self,
        *,
        limit: int = 50,
        category: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        symbol: str | None = None,
        published_since: datetime | None = None,
        published_until: datetime | None = None,
    ) -> list[Event]:
        stmt = (
            select(Event)
            .order_by(Event.published_at.desc().nullslast(), Event.id.desc())
            .limit(limit)
        )
        if category:
            stmt = stmt.where(Event.category == category)
        if status:
            stmt = stmt.where(Event.status == status)
        if severity:
            stmt = stmt.where(Event.severity == severity)
        if published_since:
            stmt = stmt.where(Event.published_at >= published_since)
        if published_until:
            stmt = stmt.where(Event.published_at <= published_until)
        if symbol and self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.where(Event.symbols.any(symbol.upper()))
        return list(self.session.scalars(stmt))

    def get(self, event_id: int) -> Event | None:
        return self.session.scalar(
            select(Event).options(selectinload(Event.sources)).where(Event.id == event_id)
        )

    def get_by_key(self, event_key: str) -> Event | None:
        return self.session.scalar(select(Event).where(Event.event_key == event_key))
