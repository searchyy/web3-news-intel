from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Delivery, Event, Source
from app.db.session import get_session
from app.observability.metrics import db_deliveries, db_events, db_sources

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics(session: Session = Depends(get_session)) -> Response:
    source_count = session.scalar(select(func.count(Source.id))) or 0
    event_count = session.scalar(select(func.count(Event.id))) or 0
    delivery_count = session.scalar(select(func.count(Delivery.id))) or 0
    db_sources.set(source_count)
    db_events.set(event_count)
    db_deliveries.set(delivery_count)
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
