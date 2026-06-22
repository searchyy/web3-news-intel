from __future__ import annotations

import httpx
import pytest
from argon2 import PasswordHasher

from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import AIProviderConfig
from app.db.session import get_session
from app.main import app


@pytest.mark.asyncio
async def test_admin_ai_config_crud_masks_and_deletes_key(monkeypatch, db_session) -> None:
    _login_failures.clear()
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/admin/auth/login",
                json={"username": "admin", "password": "password"},
            )
            csrf = login.json()["csrf_token"]
            saved = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={
                    "enabled": True,
                    "api_key": "sk-admin-secret",
                    "model": "deepseek-chat",
                    "daily_token_budget": 500,
                },
                headers={"x-csrf-token": csrf},
            )
            assert saved.status_code == 200
            assert "sk-admin-secret" not in saved.text
            body = saved.json()
            assert body["api_key_configured"] is True
            assert body["api_key_masked"].startswith("sha256:")

            row = db_session.query(AIProviderConfig).filter_by(provider="deepseek").one()
            assert row.api_key_ciphertext
            assert "sk-admin-secret" not in row.api_key_ciphertext

            loaded = await client.get("/api/admin/ai/providers/deepseek")
            assert loaded.status_code == 200
            assert "sk-admin-secret" not in loaded.text
            assert loaded.json()["api_key_configured"] is True

            deleted = await client.delete(
                "/api/admin/ai/providers/deepseek/key",
                headers={"x-csrf-token": csrf},
            )
            assert deleted.status_code == 200
            assert deleted.json()["api_key_configured"] is False
            assert row.api_key_ciphertext is None
    finally:
        app.dependency_overrides.clear()
        _login_failures.clear()
