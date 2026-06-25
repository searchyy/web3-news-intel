from __future__ import annotations

import httpx
import pytest
import redis
from argon2 import PasswordHasher

from app.api.routes import admin_api
from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.db.session import get_session
from app.main import app


@pytest.mark.asyncio
async def test_system_health_checks_available_dependencies(monkeypatch, db_session) -> None:
    client = await _logged_in_client(monkeypatch, db_session)
    monkeypatch.setattr(
        admin_api.redis.Redis, "from_url", lambda *_args, **_kwargs: _HealthyRedis()
    )
    monkeypatch.setattr(admin_api, "celery_app", _FakeCelery(control=_HealthyCeleryControl()))

    try:
        response = await client.get("/api/admin/system/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["api"] == "ok"
        assert payload["postgresql"] == "ok"
        assert payload["redis"] == "ok"
        assert payload["celery"] == "ok"
        assert payload["status"] == "ok"
        assert payload["degraded"] == "false"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_system_health_degrades_when_db_redis_celery_unavailable(
    monkeypatch, db_session
) -> None:
    client = await _logged_in_client(monkeypatch, db_session)

    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    def override_broken_session():
        yield BrokenSession()

    app.dependency_overrides[get_session] = override_broken_session
    _install_unavailable_redis(monkeypatch)
    monkeypatch.setattr(admin_api, "celery_app", _FakeCelery(control=_BrokenCeleryControl()))

    try:
        response = await client.get("/api/admin/system/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["postgresql"] == "error"
        assert payload["redis"] == "error"
        assert payload["celery"] == "error"
        assert payload["status"] == "degraded"
        assert payload["degraded"] == "true"
        assert "postgresql:" in payload["error"]
        assert "redis:" in payload["error"]
        assert "celery:" in payload["error"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_system_queues_reads_default_queue_depth_from_redis(monkeypatch, db_session) -> None:
    client = await _logged_in_client(monkeypatch, db_session)
    redis_client = _QueueRedis(depth=7)
    monkeypatch.setattr(admin_api.redis.Redis, "from_url", lambda *_args, **_kwargs: redis_client)
    monkeypatch.setattr(admin_api, "celery_app", _FakeCelery(queue_name="web3-news-intel"))

    try:
        response = await client.get("/api/admin/system/queues")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["degraded"] is False
        assert payload["queues"] == [
            {
                "name": "web3-news-intel",
                "depth": 7,
                "status": "ok",
                "error": None,
            }
        ]
        assert redis_client.llen_keys == ["web3-news-intel"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_system_queues_degrades_when_redis_unavailable(monkeypatch, db_session) -> None:
    client = await _logged_in_client(monkeypatch, db_session)
    _install_unavailable_redis(monkeypatch)
    monkeypatch.setattr(admin_api, "celery_app", _FakeCelery(queue_name="web3-news-intel"))

    try:
        response = await client.get("/api/admin/system/queues")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "degraded"
        assert payload["degraded"] is True
        assert payload["queues"][0]["name"] == "web3-news-intel"
        assert payload["queues"][0]["depth"] is None
        assert payload["queues"][0]["status"] == "error"
        assert "RedisError" in payload["error"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def _logged_in_client(monkeypatch, db_session) -> httpx.AsyncClient:
    _login_failures.clear()
    admin_api.session_store._memory.clear()
    admin_api.session_store._redis = None
    admin_api.session_store._redis_unavailable_until = 0.0
    _install_unavailable_redis(monkeypatch)
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    response = await client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password"},
    )
    assert response.status_code == 200
    return client


def _install_unavailable_redis(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise redis.RedisError("redis unavailable")

    monkeypatch.setattr(admin_api.redis.Redis, "from_url", unavailable)


class _HealthyRedis:
    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


class _QueueRedis(_HealthyRedis):
    def __init__(self, *, depth: int) -> None:
        self.depth = depth
        self.llen_keys: list[str] = []

    def llen(self, key: str) -> int:
        self.llen_keys.append(key)
        return self.depth


class _FakeCeleryConf:
    def __init__(self, *, queue_name: str, broker_url: str) -> None:
        self.task_default_queue = queue_name
        self.broker_url = broker_url


class _FakeCelery:
    def __init__(
        self,
        *,
        queue_name: str = "web3-news-intel",
        broker_url: str = "redis://localhost:6379/0",
        control=None,
    ) -> None:
        self.conf = _FakeCeleryConf(queue_name=queue_name, broker_url=broker_url)
        self.control = control or _HealthyCeleryControl()


class _HealthyCeleryControl:
    def inspect(self, timeout: float):
        assert timeout == 1.0
        return _HealthyCeleryInspector()


class _HealthyCeleryInspector:
    def ping(self) -> dict[str, dict[str, str]]:
        return {"worker-a": {"ok": "pong"}}


class _BrokenCeleryControl:
    def inspect(self, timeout: float):
        assert timeout == 1.0
        raise RuntimeError("celery unavailable")