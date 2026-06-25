from __future__ import annotations

import json

import httpx
import pytest

from app.db.models import Event, EventAIInsight
from app.integrations.feishu.card_renderer import render_event_card, render_event_text
from app.integrations.feishu.client import FeishuClient, validate_feishu_webhook_url
from app.integrations.feishu.errors import FeishuConfigurationError
from app.integrations.feishu.signatures import sign_custom_webhook
from app.integrations.feishu.token_provider import FeishuTokenProvider


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.store[key] = value
        return True

    async def delete(self, key: str):
        self.deleted.append(key)
        self.store.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_token_cache_hit_avoids_http() -> None:
    fake_redis = FakeAsyncRedis()
    provider = FeishuTokenProvider(
        app_id="app-id",
        app_secret="app-secret",
        redis_client=fake_redis,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )
    fake_redis.store[provider.cache_key] = "cached-token"
    assert await provider.get_token() == "cached-token"


@pytest.mark.asyncio
async def test_app_message_payload_and_token_retry(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "tenant_access_token" in str(request.url):
            return httpx.Response(200, json={"tenant_access_token": "token", "expire": 3600})
        if len([call for call in calls if "messages" in str(call.url)]) == 1:
            return httpx.Response(401, json={"code": 99991663})
        return httpx.Response(200, json={"data": {"message_id": "om_test"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = FeishuTokenProvider(
        app_id="app-id",
        app_secret="app-secret",
        redis_client=FakeAsyncRedis(),
        client=client,
    )
    feishu = FeishuClient(token_provider=provider, client=client)
    result = await feishu.send_interactive_card("oc_test", {"elements": []})
    assert result.ok is True
    assert result.message_id == "om_test"
    sent = [call for call in calls if "messages" in str(call.url)][-1]
    payload = json.loads(sent.content)
    assert payload["receive_id"] == "oc_test"
    assert payload["msg_type"] == "interactive"
    assert isinstance(payload["content"], str)


def test_custom_webhook_signing() -> None:
    assert sign_custom_webhook(123, "secret")


def test_custom_webhook_url_requires_https() -> None:
    with pytest.raises(FeishuConfigurationError):
        validate_feishu_webhook_url("http://open.feishu.cn/blocked-placeholder")


def test_card_rendering_escapes_and_bounds() -> None:
    event = Event(
        id=1,
        event_key="security:test",
        title="<b>unsafe</b>" * 100,
        summary="<script>alert(1)</script>" * 50,
        category="security",
        status="confirmed",
        severity="critical",
        trust_score=90,
        confirmation_count=2,
        symbols=["ETH"],
        chains=[],
        entities=[],
        metadata_={},
    )
    card = render_event_card(event)
    text = json.dumps(card)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert render_event_text(event)


def test_event_card_prefers_ai_summary_over_event_fallback() -> None:
    event = Event(
        id=1,
        event_key="security:ai-summary",
        title="安全事件",
        summary="基础摘要",
        category="security",
        status="confirmed",
        severity="high",
        trust_score=92,
        confirmation_count=2,
        symbols=["ETH"],
        chains=[],
        entities=[],
        metadata_={},
        ai_insights=[
            EventAIInsight(
                provider="deepseek",
                model="mock",
                prompt_version="v1",
                input_hash="input",
                summary_zh="AI 风险摘要",
                status="success",
            )
        ],
    )

    card_text = json.dumps(render_event_card(event), ensure_ascii=False)
    plain_text = render_event_text(event)

    assert "AI 风险摘要" in card_text
    assert "AI 风险摘要" in plain_text
    assert "基础摘要" not in card_text
