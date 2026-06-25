from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def make_engine(url: str | None = None) -> Engine:
    resolved_url = url or settings.database_url
    connect_args = {}
    if resolved_url.startswith("sqlite"):
        connect_args = {"timeout": 60}
    created = create_engine(
        resolved_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    if resolved_url.startswith("sqlite"):
        _configure_sqlite(created)
    return created


def _configure_sqlite(created: Engine) -> None:
    @event.listens_for(created, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
