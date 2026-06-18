from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.repositories.source_repo import SourceRepository
from app.db.session import get_session
from app.schemas.source import SourceRead

router = APIRouter(tags=["sources"])


@router.get("/sources", response_model=list[SourceRead])
def list_sources(
    enabled: bool | None = None,
    session: Session = Depends(get_session),
) -> list[SourceRead]:
    sources = SourceRepository(session).list(enabled=enabled)
    return [SourceRead.model_validate(source) for source in sources]
