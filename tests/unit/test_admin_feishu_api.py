from __future__ import annotations

import json

import httpx
import pytest
from argon2 import PasswordHasher

from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.db.session import get_session
from app.main import app
from app.schemas.admin import DestinationRead


@pytest.mark.asyncio
async def test_admin_login_csrf_and_me(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/admin/auth/login",
                json={"username": "admin", "password": "password"},
            )
            assert response.status_code == 200
            csrf = response.json()["csrf_token"]
            me = await client.get("/api/admin/auth/me")
            assert me.status_code == 200
            logout = await client.post("/api/admin/auth/logout", headers={"x-csrf-token": csrf})
            assert logout.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_login_rate_limits_unknown_username(monkeypatch, db_session) -> None:
    _login_failures.clear()
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(5):
                response = await client.post(
                    "/api/admin/auth/login",
                    json={"username": "intruder", "password": "wrong"},
                )
                assert response.status_code == 401
                assert response.json()["detail"] == "invalid username or password"
            response = await client.post(
                "/api/admin/auth/login",
                json={"username": "intruder", "password": "wrong"},
            )
            assert response.status_code == 429
    finally:
        app.dependency_overrides.clear()
        _login_failures.clear()


@pytest.mark.asyncio
async def test_feishu_group_callback_registers_pending_destination(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "verify")

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        payload = {
            "token": "verify",
            "header": {"event_id": "evt-1", "event_type": "im.chat.member.bot.added_v1"},
            "event": {"chat": {"chat_id": "oc_test", "name": "Test group"}},
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/integrations/feishu/events",
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 200
            duplicate = await client.post(
                "/integrations/feishu/events",
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )
            assert duplicate.json()["status"] == "duplicate"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feishu_callback_requires_verification_configuration(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", None)
    monkeypatch.setattr(settings, "feishu_encrypt_key", None)

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        payload = {
            "header": {"event_id": "evt-unverified", "event_type": "im.chat.member.bot.added_v1"},
            "event": {"chat": {"chat_id": "oc_test", "name": "Test group"}},
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/integrations/feishu/events",
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_destination_read_redacts_secret_config_fields() -> None:
    destination = DestinationRead.model_validate(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "key": "dest",
            "name": "Destination",
            "provider": "feishu_webhook",
            "enabled": False,
            "status": "pending",
            "chat_id": None,
            "chat_name": None,
            "secret_fingerprint": "abcdef1234567890",
            "config": {
                "mode": "test",
                "api_token": "placeholder-token",
                "nested": {"webhook_url": "placeholder-url"},
            },
            "activated_at": None,
            "last_tested_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "last_error_code": None,
            "last_error_message": None,
            "created_at": "2026-06-20T00:00:00Z",
            "updated_at": "2026-06-20T00:00:00Z",
        }
    )
    assert destination.config["mode"] == "test"
    assert destination.config["api_token"] == "[redacted]"
    assert destination.config["nested"]["webhook_url"] == "[redacted]"
    assert destination.secret_fingerprint != "abcdef1234567890"
