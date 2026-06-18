from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import SourceConfig
from app.core.time import utc_now
from app.db.models import Source


class SourceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_key(self, key: str) -> Source | None:
        return self.session.scalar(select(Source).where(Source.key == key))

    def list(self, *, enabled: bool | None = None) -> list[Source]:
        stmt = select(Source).order_by(Source.key)
        if enabled is not None:
            stmt = stmt.where(Source.enabled.is_(enabled))
        return list(self.session.scalars(stmt))

    def upsert_from_config(self, config: SourceConfig) -> Source:
        source = self.get_by_key(config.key)
        values = config.model_dump()
        if source is None:
            source = Source(**values)
            self.session.add(source)
            return source
        for field, value in values.items():
            setattr(source, field, value)
        source.updated_at = utc_now()
        return source

    def mark_access_denied(self, source: Source, reason: str) -> None:
        source.enabled = False
        source.access_denied_at = utc_now()
        source.access_denied_reason = reason
        source.updated_at = utc_now()
