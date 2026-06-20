from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.base import Base

_skipped_reports = 0


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    with SessionLocal() as session:
        yield session


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "postgres: requires a real PostgreSQL database")
    config.addinivalue_line("markers", "redis: requires a real Redis broker")
    config.addinivalue_line("markers", "celery: requires a real Celery worker/broker")
    config.addinivalue_line("markers", "compose: requires a real Docker Compose stack")
    config.addinivalue_line("markers", "live: touches public live source endpoints")


@pytest.fixture(autouse=True)
def test_environment_http_overrides(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "http_allow_localhost", False)
    monkeypatch.setattr(settings, "http_validate_dns_rebinding", False)


def pytest_runtest_logreport(report) -> None:
    global _skipped_reports
    if report.skipped:
        _skipped_reports += 1


def pytest_sessionfinish(session, exitstatus) -> None:
    if os.environ.get("PYTEST_FAIL_ON_SKIP") == "1" and _skipped_reports:
        session.exitstatus = 1


@pytest.fixture()
def postgres_session(monkeypatch) -> Session:
    import os

    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL must point to PostgreSQL")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
