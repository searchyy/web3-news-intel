from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import load_sources
from app.core.security import require_admin
from app.db.repositories.event_repo import EventRepository
from app.db.repositories.source_repo import SourceRepository
from app.db.session import get_session
from app.schemas.api import ReloadResponse, RepublishResponse
from app.workers.tasks_publish import republish_event

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/sources/reload", response_model=ReloadResponse)
def reload_sources(session: Session = Depends(get_session)) -> ReloadResponse:
    sources_file = load_sources()
    repo = SourceRepository(session)
    for source in sources_file.sources.values():
        repo.upsert_from_config(source)
    session.commit()
    return ReloadResponse(
        loaded=len(sources_file.sources), enabled=len(sources_file.enabled_sources())
    )


@router.post("/events/{event_id}/republish", response_model=RepublishResponse)
def republish(event_id: int, session: Session = Depends(get_session)) -> RepublishResponse:
    event = EventRepository(session).get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    republish_event.delay(event_id)
    return RepublishResponse(event_id=event_id, queued=True)
