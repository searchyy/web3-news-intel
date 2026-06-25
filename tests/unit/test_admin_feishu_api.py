from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from argon2 import PasswordHasher

from app.api.routes import admin_api
from app.core.admin_auth import _login_failures
from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import Event, NotificationDestination
from app.db.session import get_session
from app.main import app
from app.schemas.admin import DestinationRead, FeishuTestResult
from app.schemas.event import EventRead


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
                assert response.json()["detail"] == "用户名或密码错误"
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


def test_event_read_exposes_chinese_display_fields() -> None:
    event = Event(
        id=1,
        event_key="zh:event",
        title="以太坊基金会发布升级公告",
        summary="协议升级摘要",
        category="protocol",
        status="confirmed",
        severity="high",
        language="zh-CN",
        trust_score=90,
        confirmation_count=1,
        symbols=["ETH"],
        chains=["Ethereum"],
        entities=[],
        metadata_={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    payload = EventRead.model_validate(event).model_dump()
    assert payload["display_title"] == "以太坊基金会发布升级公告"
    assert payload["display_summary"] == "协议升级摘要"
    assert payload["category_label"] == "协议更新"
    assert payload["severity_label"] == "高"
    assert payload["status_label"] == "已确认"


@pytest.mark.asyncio
async def test_notification_rule_defaults_disabled_and_activation_starts_on_enable(
    monkeypatch, db_session
) -> None:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)
    old_activation = datetime(2026, 6, 20, tzinfo=UTC)
    destination = NotificationDestination(
        key="feishu-rule-test",
        name="飞书测试群",
        provider="feishu_webhook",
        enabled=True,
        status="active",
        activated_at=old_activation,
        config={},
    )
    db_session.add(destination)
    db_session.flush()

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
            created = await client.post(
                "/api/admin/rules",
                json={"destination_id": str(destination.id), "name": "高风险即时通知"},
                headers={"x-csrf-token": csrf},
            )
            assert created.status_code == 200
            body = created.json()
            assert body["enabled"] is False
            db_session.refresh(destination)
            assert destination.activated_at is not None
            assert destination.activated_at.replace(tzinfo=UTC) == old_activation

            enabled = await client.patch(
                f"/api/admin/rules/{body['id']}",
                json={"enabled": True},
                headers={"x-csrf-token": csrf},
            )
            assert enabled.status_code == 200
            db_session.refresh(destination)
            assert destination.activated_at is not None
            assert destination.activated_at.replace(tzinfo=UTC) > old_activation
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feishu_config_save_and_load_masks_secrets(monkeypatch, db_session) -> None:
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
            payload = {
                "FEISHU_APP_ID": "cli_a_test",
                "FEISHU_APP_SECRET": "app-secret-value",
                "FEISHU_VERIFICATION_TOKEN": "verify-token",
                "FEISHU_ENCRYPT_KEY": "encrypt-key",
                "FEISHU_TEST_CHAT_ID": "oc_test",
                "FEISHU_ENABLED": True,
                "FEISHU_SEND_ENABLED": False,
            }
            saved = await client.post(
                "/api/admin/system/feishu-config",
                json=payload,
                headers={"x-csrf-token": csrf},
            )
            assert saved.status_code == 200
            body = saved.json()
            assert body["FEISHU_APP_ID"] == "cli_a_test"
            assert body["FEISHU_APP_SECRET"] == "****alue"
            assert body["FEISHU_VERIFICATION_TOKEN"] == "****oken"
            assert body["FEISHU_ENCRYPT_KEY"] == "****-key"
            assert "app-secret-value" not in saved.text

            loaded = await client.get("/api/admin/system/feishu-config")
            assert loaded.json()["FEISHU_APP_SECRET"] == "****alue"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_feishu_test_connection_success_and_failure(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", PasswordHasher().hash("password"))
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "admin_secure_cookie", False)
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())

    async def fake_success(_config):
        return FeishuTestResult(status="success", latency_ms=12, message="连接成功")

    monkeypatch.setattr(admin_api, "_run_feishu_connection_test", fake_success)

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
            await client.post(
                "/api/admin/system/feishu-config",
                json={
                    "FEISHU_APP_ID": "cli_a_test",
                    "FEISHU_APP_SECRET": "app-secret-value",
                    "FEISHU_TEST_CHAT_ID": "oc_test",
                    "FEISHU_ENABLED": True,
                    "FEISHU_SEND_ENABLED": True,
                },
                headers={"x-csrf-token": csrf},
            )
            success = await client.post(
                "/api/admin/destinations/test-feishu",
                headers={"x-csrf-token": csrf},
            )
            assert success.status_code == 200
            assert success.json()["status"] == "success"
            assert success.json()["message"] == "连接成功"

            async def fake_failure(_config):
                return FeishuTestResult(status="failed", error="invalid_app_secret")

            monkeypatch.setattr(admin_api, "_run_feishu_connection_test", fake_failure)
            failure = await client.post(
                "/api/admin/destinations/test-feishu",
                headers={"x-csrf-token": csrf},
            )
            assert failure.status_code == 200
            assert failure.json()["status"] == "failed"
            assert failure.json()["error"] == "invalid_app_secret"

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_i18n_default_zh_and_accept_language_en(db_session) -> None:
    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            zh = await client.get("/events/999999")
            assert zh.status_code == 404
            assert zh.json()["detail"] == "事件不存在"

            en = await client.get("/events/999999", headers={"accept-language": "en-US"})
            assert en.status_code == 404
            assert en.json()["detail"] == "event not found"
    finally:
        app.dependency_overrides.clear()
