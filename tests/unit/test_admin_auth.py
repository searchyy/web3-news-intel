from __future__ import annotations

import pytest
import redis
from fastapi import HTTPException

from app.core.admin_auth import AdminSessionStore
from app.core.config import settings


def test_local_admin_session_caches_redis_unavailable(monkeypatch) -> None:
    calls = 0

    def unavailable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise redis.RedisError("redis unavailable")

    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(redis.Redis, "from_url", unavailable)

    store = AdminSessionStore()
    session_id, _csrf = store.create("admin")

    assert calls == 1
    assert store.get(session_id)["subject"] == "admin"
    assert calls == 1


def test_production_admin_session_requires_redis(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise redis.RedisError("redis unavailable")

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(redis.Redis, "from_url", unavailable)

    with pytest.raises(HTTPException) as exc:
        AdminSessionStore().create("admin")
    assert exc.value.status_code == 503


def test_local_admin_session_falls_back_when_cached_redis_fails(monkeypatch) -> None:
    class FlakyRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.fail_get = False

        def ping(self) -> bool:
            return True

        def setex(self, key: str, _ttl: int, value: str) -> None:
            self.values[key] = value

        def get(self, key: str):
            if self.fail_get:
                raise redis.RedisError("redis disconnected")
            return self.values.get(key)

        def delete(self, key: str) -> None:
            self.values.pop(key, None)

    client = FlakyRedis()
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:6379/0")
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: client)

    store = AdminSessionStore()
    session_id, _csrf = store.create("admin")
    assert store.get(session_id)["subject"] == "admin"

    client.fail_get = True
    assert store.get(session_id) is None

    fallback_session_id, _csrf = store.create("admin")
    assert store.get(fallback_session_id)["subject"] == "admin"


def test_staging_admin_session_does_not_use_memory_fallback(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise redis.RedisError("redis unavailable")

    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(redis.Redis, "from_url", unavailable)

    with pytest.raises(HTTPException) as exc:
        AdminSessionStore().create("admin")
    assert exc.value.status_code == 503
