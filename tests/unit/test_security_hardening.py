from __future__ import annotations

import json
import socket

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings, SourceConfig
from app.core.errors import FetchError
from app.core.security import require_admin
from app.core.url_security import validate_public_http_url
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


def test_enabled_source_dns_failure_fails_validation(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_validate_dns_rebinding", True)

    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("deterministic dns failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError):
        _source(url="https://example.com/feed.xml", canonical_url="https://example.com/feed.xml")


def test_disabled_source_skips_dns_availability_check(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.http_validate_dns_rebinding", True)

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
    assert settings.allow_private_networks is False
    assert settings.http_allow_localhost is False


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
