from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import Event
from app.integrations.ai.base import AIMessage
from app.integrations.ai.deepseek.client import DeepSeekClient
from app.integrations.ai.deepseek.errors import (
    AIAuthenticationError,
    AIBudgetExceededError,
    AIRateLimitedError,
    AITimeoutError,
    AITransientError,
)
from app.integrations.ai.service import (
    AIConfigurationError,
    AIService,
    build_event_input,
    provider_config_to_public_dict,
    sanitize_error,
)


@pytest.mark.asyncio
async def test_deepseek_client_lists_models_and_sends_chat_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("paths", []).append(request.url.path)
        seen["authorization"] = request.headers.get("authorization")
        if request.url.path == "/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "deepseek-chat", "owned_by": "deepseek"}]},
                request=request,
            )
        payload = json.loads(request.content)
        seen["chat_payload"] = payload
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    deepseek = DeepSeekClient(
        api_base="https://api.deepseek.com",
        api_key="sk-test",
        timeout_seconds=5,
        client=client,
    )
    models = await deepseek.list_models()
    result = await deepseek.chat_completion(
        model="deepseek-chat",
        messages=[AIMessage(role="user", content="hello")],
        max_tokens=256,
        temperature=0.2,
        thinking_enabled=True,
    )
    await client.aclose()

    assert models[0].id == "deepseek-chat"
    assert seen["authorization"] == "Bearer sk-test"
    assert seen["chat_payload"]["response_format"] == {"type": "json_object"}
    assert seen["chat_payload"]["thinking"] == {"enabled": True}
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7


@pytest.mark.asyncio
async def test_deepseek_client_maps_429_to_retryable_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    deepseek = DeepSeekClient(
        api_base="https://api.deepseek.com",
        api_key="sk-test",
        timeout_seconds=5,
        client=client,
    )
    with pytest.raises(AIRateLimitedError) as exc_info:
        await deepseek.list_models()
    await client.aclose()

    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 7


@pytest.mark.asyncio
async def test_deepseek_client_maps_5xx_and_timeout_to_retryable_errors() -> None:
    def transient_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    transient_client = httpx.AsyncClient(transport=httpx.MockTransport(transient_handler))
    transient = DeepSeekClient(
        api_base="https://api.deepseek.com",
        api_key="sk-test",
        timeout_seconds=5,
        client=transient_client,
    )
    with pytest.raises(AITransientError) as transient_exc:
        await transient.list_models()
    await transient_client.aclose()
    assert transient_exc.value.retryable is True

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    timeout = DeepSeekClient(
        api_base="https://api.deepseek.com",
        api_key="sk-test",
        timeout_seconds=5,
        client=timeout_client,
    )
    with pytest.raises(AITimeoutError) as timeout_exc:
        await timeout.list_models()
    await timeout_client.aclose()
    assert timeout_exc.value.retryable is True


def test_ai_service_requires_field_encryption_key_for_new_key(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", None)
    service = AIService(db_session)

    with pytest.raises(AIConfigurationError) as exc_info:
        service.save_provider_config({"api_key": "sk-missing-secret", "model": "deepseek-chat"})

    sanitized = sanitize_error(exc_info.value)
    assert "缺少 FIELD_ENCRYPTION_KEY" in sanitized
    assert "sk-missing-secret" not in sanitized
    assert "Traceback" not in sanitized


def test_ai_service_ignores_empty_and_masked_key_updates(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    service = AIService(db_session)
    config = service.save_provider_config(
        {"api_key": "sk-original-secret", "model": "deepseek-chat"}
    )
    original_ciphertext = config.api_key_ciphertext
    original_fingerprint = config.api_key_fingerprint
    assert original_ciphertext
    assert original_fingerprint

    public = provider_config_to_public_dict(config, service.usage_today("deepseek"))
    masked_updates = (
        "",
        "   ",
        "****",
        public["api_key_masked"],
        public["api_key_fingerprint"],
    )
    for masked_value in masked_updates:
        config = service.save_provider_config(
            {"api_key": masked_value, "model": "deepseek-reasoner"}
        )
        assert config.api_key_ciphertext == original_ciphertext
        assert config.api_key_fingerprint == original_fingerprint
        assert config.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_ai_service_lists_models_with_saved_database_key(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    seen: dict[str, Any] = {"paths": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["paths"].append(request.url.path)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat", "owned_by": "deepseek"}]},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AIService(db_session, http_client=client)
    service.save_provider_config({"api_key": "sk-db-saved", "model": "deepseek-chat"})

    models = await service.list_models("deepseek")
    await client.aclose()

    assert models == [
        {"id": "deepseek-chat", "owned_by": "deepseek", "metadata": {}}
    ]
    assert seen["paths"] == ["/models"]
    assert seen["authorization"] == "Bearer sk-db-saved"


@pytest.mark.asyncio
async def test_ai_service_test_connection_persists_success_and_failure(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())

    def success_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-connection"
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat", "owned_by": "deepseek"}]},
            request=request,
        )

    success_client = httpx.AsyncClient(transport=httpx.MockTransport(success_handler))
    service = AIService(db_session, http_client=success_client)
    config = service.save_provider_config({"api_key": "sk-connection", "model": "deepseek-chat"})

    result = await service.test_connection("deepseek")
    await success_client.aclose()

    assert result["status"] == "success"
    assert result["model_count"] == 1
    assert config.last_test_status == "success"
    assert config.last_error_sanitized is None

    def failure_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-connection"
        return httpx.Response(401, request=request)

    failure_client = httpx.AsyncClient(transport=httpx.MockTransport(failure_handler))
    failing_service = AIService(db_session, http_client=failure_client)
    with pytest.raises(AIAuthenticationError):
        await failing_service.test_connection("deepseek")
    await failure_client.aclose()

    assert config.last_test_status == "failed"
    assert config.last_error_sanitized
    assert "ai_authentication_failed" in config.last_error_sanitized
    assert "sk-connection" not in config.last_error_sanitized


@pytest.mark.asyncio
async def test_ai_service_encrypts_key_masks_output_and_repairs_invalid_json(
    monkeypatch,
    db_session,
) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    calls = {"chat": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-unit"
        calls["chat"] += 1
        if calls["chat"] == 1:
            content = "not-json"
        else:
            content = json.dumps(
                {
                    "headline_zh": "BTC 事件",
                    "summary_zh": "BTC 出现重要市场事件。",
                    "key_facts": [{"text": "公告已发布"}],
                    "entities": [],
                    "symbols": ["BTC"],
                    "chains": ["Bitcoin"],
                    "event_type": "market",
                    "importance_score": 80,
                    "risk_level": "medium",
                    "sentiment": "mixed",
                    "market_impact": "不确定",
                    "facts": [{"text": "来源标题包含 BTC"}],
                    "inferences": [{"text": "可能影响短期情绪"}],
                    "confidence": 0.8,
                    "source_event_ids": ["999999"],
                    "source_urls": ["https://evil.example/not-input"],
                },
                ensure_ascii=False,
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            request=request,
        )

    event = _event()
    db_session.add(event)
    db_session.flush()
    service = AIService(
        db_session,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    config = service.save_provider_config(
        {
            "enabled": True,
            "api_key": "sk-unit",
            "model": "deepseek-chat",
            "daily_token_budget": 1000,
        }
    )
    assert config.api_key_ciphertext and "sk-unit" not in config.api_key_ciphertext
    public = provider_config_to_public_dict(config, service.usage_today("deepseek"))
    assert public["api_key_configured"] is True
    assert public["api_key_masked"].startswith("sha256:")
    assert public["api_key_fingerprint"] == f"sha256:{config.api_key_fingerprint[:16]}..."
    assert "sk-unit" not in json.dumps(public, default=str)
    assert config.api_key_fingerprint not in json.dumps(public, default=str)

    insight = await service.summarize_event(event.id)
    assert insight.summary_zh == "BTC 出现重要市场事件。"
    assert insight.source_event_ids == [str(event.id)]
    assert insight.source_urls == [event.primary_url]
    assert insight.prompt_tokens == 20
    assert insight.completion_tokens == 10

    again = await service.summarize_event(event.id)
    assert again.id == insight.id
    assert calls["chat"] == 2
    await service.http_client.aclose()


@pytest.mark.asyncio
async def test_ai_auto_processing_zero_budget_rejected(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "field_encryption_key", FieldEncryptor.generate_key())
    event = _event()
    db_session.add(event)
    db_session.flush()
    service = AIService(db_session)
    service.save_provider_config(
        {
            "enabled": True,
            "auto_process_enabled": True,
            "api_key": "sk-unit",
            "model": "deepseek-chat",
            "daily_token_budget": 0,
        }
    )
    with pytest.raises(AIBudgetExceededError):
        await service.summarize_event(event.id, auto=True)


def test_ai_input_redacts_secret_metadata() -> None:
    event = _event()
    event.id = 1
    event.metadata_ = {"api_token": "secret", "safe": "value", "raw_html": "<html>"}
    payload = build_event_input(event)
    assert payload.metadata["api_token"] == "[redacted]"
    assert payload.metadata["raw_html"] == "[redacted]"
    assert payload.metadata["safe"] == "value"


def _event() -> Event:
    return Event(
        event_key="ai:test",
        title="BTC market event",
        summary="BTC summary",
        category="market",
        status="confirmed",
        severity="high",
        language="en",
        primary_url="https://example.com/btc",
        published_at=datetime.now(UTC),
        trust_score=80,
        confirmation_count=1,
        symbols=["BTC"],
        chains=["Bitcoin"],
        entities=[],
        metadata_={},
    )
