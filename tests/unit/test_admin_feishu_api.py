from __future__ import annotations

import json

import httpx
import pytest
from argon2 import PasswordHasher

from app.core.config import settings
from app.db.session import get_session
from app.main import app


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
