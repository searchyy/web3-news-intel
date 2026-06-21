from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings, SourceConfig, settings
from app.core.errors import FetchError
from app.core.security import require_admin
from app.core.url_security import validate_public_http_url
from app.integrations.ai.settings import validate_deepseek_api_base
from app.integrations.feishu.client import FeishuClient
from app.integrations.feishu.errors import FeishuConfigurationError
from app.publishers.webhook import WebhookPublisher


def test_admin_requires_configured_token(monkeypatch) -> None:
    monkeypatch.setattr("app.core.security.settings.admin_token", None)
    with pytest.raises(HTTPException) as exc:
        require_admin(None)
    assert exc.value.status_code == 503


def test_admin_uses_static_token(monkeypatch) -> None:
    monkeypatch.setattr("app.core.security.settings.admin_token", "secret")
    require_admin("secret")
    with pytest.raises(HTTPException) as exc:
        require_admin("wrong")
    assert exc.value.status_code == 401


def test_source_config_blocks_private_networks_by_default(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_allow_localhost", False)
    with pytest.raises(ValueError):
        _source(url="http://127.0.0.1/feed.xml", canonical_url="http://127.0.0.1/feed.xml")


def test_source_config_allows_localhost_when_explicit(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_allow_localhost", True)
    source = _source(
        url="http://127.0.0.1/feed.xml",
        canonical_url="http://127.0.0.1/feed.xml",
        allow_private_networks=True,
        allow_localhost=True,
    )
    assert source.allow_private_networks is True
    assert source.allow_localhost is True


def test_enabled_source_skips_dns_availability_check_by_default(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_validate_dns_rebinding", True)
    monkeypatch.setattr("app.core.config.settings.http_validate_source_dns_on_load", False)

    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("deterministic dns failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    source = _source(url="https://example.com/feed.xml", canonical_url="https://example.com/feed.xml")
    assert source.enabled is True


def test_source_config_can_opt_into_dns_validation_on_load(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_validate_source_dns_on_load", True)

    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("deterministic dns failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError):
        _source(url="https://example.com/feed.xml", canonical_url="https://example.com/feed.xml")


def test_disabled_source_skips_dns_availability_check(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_validate_source_dns_on_load", True)

    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("deterministic dns failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    source = _source(
        url="https://example.com/feed.xml",
        canonical_url="https://example.com/feed.xml",
        enabled=False,
    )
    assert source.enabled is False


def test_production_url_security_defaults(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_VALIDATE_DNS_REBINDING", raising=False)
    monkeypatch.delenv("HTTP_ALLOW_PRIVATE_NETWORKS", raising=False)
    monkeypatch.delenv("HTTP_ALLOW_LOCALHOST", raising=False)
    settings = Settings(_env_file=None)
    assert settings.http_validate_dns_rebinding is True
    assert settings.http_validate_source_dns_on_load is False
    assert settings.http_trust_env is False
    assert settings.allow_private_networks is False
    assert settings.http_allow_localhost is False


def test_local_admin_cookie_is_not_secure_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_SECURE_COOKIE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.admin_secure_cookie is False


def test_production_requires_secure_admin_cookie(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "hash")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "secret")
    monkeypatch.setenv("ADMIN_SECURE_COOKIE", "false")
    with pytest.raises(ValueError, match="ADMIN_SECURE_COOKIE"):
        Settings(_env_file=None)


def test_deepseek_mock_http_requires_acceptance_flag() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_deepseek_api_base(
            "http://mock-deepseek:9001",
            allow_custom=True,
            allow_acceptance_mock=False,
        )
    assert (
        validate_deepseek_api_base(
            "http://mock-deepseek:9001",
            allow_custom=True,
            allow_acceptance_mock=True,
        )
        == "http://mock-deepseek:9001"
    )


def test_settings_allow_deepseek_mock_http_only_in_acceptance_mode(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv("ENABLE_ACCEPTANCE_TASKS", "true")
    monkeypatch.setenv("ACCEPTANCE_MOCK_HTTP_ENABLED", "true")
    monkeypatch.setenv("AI_ALLOW_CUSTOM_API_BASE", "true")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "http://mock-deepseek:9001")
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_base == "http://mock-deepseek:9001"

    monkeypatch.setenv("ACCEPTANCE_MOCK_HTTP_ENABLED", "false")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_feishu_mock_http_requires_acceptance_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "enable_acceptance_tasks", False)
    monkeypatch.setattr(settings, "acceptance_mock_http_enabled", False)
    with pytest.raises(FeishuConfigurationError, match="HTTPS"):
        FeishuClient(api_base="http://mock-feishu:9002")

    monkeypatch.setattr(settings, "app_env", "ci")
    monkeypatch.setattr(settings, "enable_acceptance_tasks", True)
    monkeypatch.setattr(settings, "acceptance_mock_http_enabled", True)
    client = FeishuClient(api_base="http://mock-feishu:9002")
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.1.1/",
        "http://224.0.0.1/",
        "http://0.0.0.0/",
        "http://240.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f000001/",
        "http://%31%32%37.0.0.1/",
    ],
)
def test_blocked_ip_forms_are_rejected(url: str) -> None:
    with pytest.raises(FetchError):
        validate_public_http_url(url)


def test_localhost_requires_explicit_allowance() -> None:
    result = validate_public_http_url("http://127.0.0.1/", allow_localhost=True)
    assert result.hostname == "127.0.0.1"


def test_dns_rebinding_rejects_if_any_address_is_private(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError):
        validate_public_http_url("https://example.com/feed", resolve_dns=True)


def test_dns_rebinding_allows_public_only_resolution(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    result = validate_public_http_url("https://example.com/feed", resolve_dns=True)
    assert result.hostname == "example.com"


def test_webhook_target_blocks_private_networks() -> None:
    with pytest.raises(FetchError):
        WebhookPublisher("http://127.0.0.1/hook")


def test_webhook_target_blocks_dns_rebinding(monkeypatch) -> None:
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError):
        WebhookPublisher("https://example.com/hook")


async def test_webhook_signing() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["signature"] = request.headers["x-webhook-signature"]
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(200, request=request)

    from app.db.models import Event

    event = Event(
        id=1,
        event_key="security:test",
        title="Security Test",
        category="listing",
        status="confirmed",
        severity="high",
        trust_score=90,
        confirmation_count=1,
        symbols=[],
        chains=[],
        entities=[],
        metadata_={},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    publisher = WebhookPublisher(
        "https://example.com/hook",
        secret="secret",
        validate_dns_rebinding=False,
        client=client,
    )
    result = await publisher.publish(event)
    assert result.ok is True
    assert seen["signature"]
    assert json.loads(seen["body"])["event_key"] == "security:test"


def _source(**overrides) -> SourceConfig:
    data = {
        "key": "security",
        "name": "Security",
        "source_type": "tier1_media",
        "adapter": "rss",
        "url": "https://example.com/feed.xml",
        "canonical_url": "https://example.com/feed.xml",
        "category": "media",
        "language": "en",
        "trust_score": 75,
        "poll_seconds": 300,
        "timeout_seconds": 15,
        "max_response_bytes": 2097152,
        "enabled": True,
        "config": {"parser_version": "security_v1"},
    }
    data.update(overrides)
    return SourceConfig(**data)
