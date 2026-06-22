from __future__ import annotations

import httpx
import pytest
from argon2 import PasswordHasher

from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import AIProviderConfig
from app.db.session import get_session
from app.integrations.ai.base import AIModelInfo
from app.integrations.ai.deepseek.client import DeepSeekClient
from app.integrations.ai.deepseek.errors import AIAuthenticationError
from app.main import app


@pytest.mark.asyncio
async def test_admin_ai_config_crud_masks_and_deletes_key(monkeypatch, db_session) -> None:
    _configure_admin(monkeypatch)
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    _override_session(db_session)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(client)
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
            assert body["api_key_fingerprint"].startswith("sha256:")
            assert body["api_key_fingerprint"].endswith("...")
            assert body["api_key_fingerprint"] != body["api_key_masked"]

            row = db_session.query(AIProviderConfig).filter_by(provider="deepseek").one()
            assert len(body["api_key_fingerprint"]) < len("sha256:" + row.api_key_fingerprint)
            assert row.api_key_ciphertext
            assert "sk-admin-secret" not in row.api_key_ciphertext
            original_ciphertext = row.api_key_ciphertext
            original_fingerprint = row.api_key_fingerprint

            loaded = await client.get("/api/admin/ai/providers/deepseek")
            assert loaded.status_code == 200
            assert "sk-admin-secret" not in loaded.text
            loaded_body = loaded.json()
            assert loaded_body["api_key_configured"] is True
            assert loaded_body["api_key_fingerprint"] == body["api_key_fingerprint"]

            empty_key_save = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={"api_key": "", "model": "deepseek-reasoner"},
                headers={"x-csrf-token": csrf},
            )
            assert empty_key_save.status_code == 200
            assert row.api_key_ciphertext == original_ciphertext
            assert row.api_key_fingerprint == original_fingerprint

            masked_key_save = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={"api_key": body["api_key_fingerprint"], "model": "deepseek-chat"},
                headers={"x-csrf-token": csrf},
            )
            assert masked_key_save.status_code == 200
            assert row.api_key_ciphertext == original_ciphertext
            assert row.api_key_fingerprint == original_fingerprint

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


@pytest.mark.asyncio
async def test_admin_ai_save_new_key_without_field_encryption_key_returns_chinese_error(
    monkeypatch,
    db_session,
) -> None:
    _configure_admin(monkeypatch)
    monkeypatch.setattr(settings, "field_encryption_key", None)
    _override_session(db_session)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(client)
            response = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={"api_key": "sk-admin-secret", "model": "deepseek-chat"},
                headers={"x-csrf-token": csrf},
            )
            assert response.status_code == 400
            assert "缺少 FIELD_ENCRYPTION_KEY" in response.text
            assert "sk-admin-secret" not in response.text
            assert "Traceback" not in response.text

            row = db_session.query(AIProviderConfig).filter_by(provider="deepseek").one_or_none()
            assert row is None or row.api_key_ciphertext is None
    finally:
        app.dependency_overrides.clear()
        _login_failures.clear()


@pytest.mark.asyncio
async def test_admin_ai_models_and_connection_use_saved_database_key(
    monkeypatch,
    db_session,
) -> None:
    _configure_admin(monkeypatch)
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    seen_keys: list[str] = []

    async def fake_list_models(self: DeepSeekClient) -> list[AIModelInfo]:
        seen_keys.append(self.api_key)
        return [AIModelInfo(id="deepseek-chat", owned_by="deepseek", metadata={})]

    monkeypatch.setattr(DeepSeekClient, "list_models", fake_list_models)
    _override_session(db_session)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(client)
            saved = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={"api_key": "sk-admin-secret", "model": "deepseek-chat"},
                headers={"x-csrf-token": csrf},
            )
            assert saved.status_code == 200

            models = await client.get("/api/admin/ai/providers/deepseek/models")
            assert models.status_code == 200
            assert models.json()[0]["id"] == "deepseek-chat"

            tested = await client.post(
                "/api/admin/ai/providers/deepseek/test",
                headers={"x-csrf-token": csrf},
            )
            assert tested.status_code == 200
            assert tested.json()["status"] == "success"

            row = db_session.query(AIProviderConfig).filter_by(provider="deepseek").one()
            assert row.last_test_status == "success"
            assert row.last_error_sanitized is None
            assert seen_keys == ["sk-admin-secret", "sk-admin-secret"]
    finally:
        app.dependency_overrides.clear()
        _login_failures.clear()


@pytest.mark.asyncio
async def test_admin_ai_connection_failure_persists_sanitized_error(
    monkeypatch,
    db_session,
) -> None:
    _configure_admin(monkeypatch)
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())

    async def fake_list_models(self: DeepSeekClient) -> list[AIModelInfo]:
        assert self.api_key == "sk-admin-secret"
        raise AIAuthenticationError("AI provider authentication failed for sk-admin-secret")

    monkeypatch.setattr(DeepSeekClient, "list_models", fake_list_models)
    _override_session(db_session)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await _login(client)
            saved = await client.put(
                "/api/admin/ai/providers/deepseek",
                json={"api_key": "sk-admin-secret", "model": "deepseek-chat"},
                headers={"x-csrf-token": csrf},
            )
            assert saved.status_code == 200

            tested = await client.post(
                "/api/admin/ai/providers/deepseek/test",
                headers={"x-csrf-token": csrf},
            )
            assert tested.status_code == 200
            body = tested.json()
            assert body["status"] == "failed"
            assert "sk-admin-secret" not in body["error"]

            row = db_session.query(AIProviderConfig).filter_by(provider="deepseek").one()
            assert row.last_test_status == "failed"
            assert row.last_error_sanitized
            assert "ai_authentication_failed" in row.last_error_sanitized
            assert "sk-admin-secret" not in row.last_error_sanitized
    finally:
        app.dependency_overrides.clear()
        _login_failures.clear()


def _configure_admin(monkeypatch) -> None:
    _login_failures.clear()
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)


def _override_session(db_session) -> None:
    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session


async def _login(client: httpx.AsyncClient) -> str:
    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password"},
    )
    assert login.status_code == 200
    return str(login.json()["csrf_token"])
