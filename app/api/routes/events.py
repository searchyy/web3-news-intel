from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.repositories.event_repo import EventRepository
from app.db.session import get_session
from app.schemas.event import EventDetail, EventRead

router = APIRouter(tags=["events"])


@router.get("/events", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    category: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    symbol: str | None = None,
    published_since: datetime | None = None,
    published_until: datetime | None = None,
    session: Session = Depends(get_session),
) -> list[EventRead]:
    events = EventRepository(session).list(
        limit=limit,
        category=category,
        status=status,
        severity=severity,
        symbol=symbol,
        published_since=published_since,
        published_until=published_until,
    )
    return [EventRead.model_validate(event) for event in events]


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(event_id: int, session: Session = Depends(get_session)) -> EventDetail:
    event = EventRepository(session).get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return EventDetail.model_validate(event)
