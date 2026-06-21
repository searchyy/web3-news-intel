from __future__ import annotations

import asyncio
import json
import random
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.core.url_security import validate_public_http_url
from app.fetch.retry import TRANSIENT_STATUS_CODES, exponential_backoff, parse_retry_after
from app.integrations.feishu.errors import (
    FeishuAuthenticationError,
    FeishuConfigurationError,
    FeishuPermanentError,
    FeishuTransientError,
)
from app.integrations.feishu.models import FeishuCard, FeishuSendResult
from app.integrations.feishu.signatures import sign_custom_webhook
from app.integrations.feishu.token_provider import FeishuTokenProvider
from app.observability.metrics import feishu_send_duration_seconds, feishu_send_total


class FeishuClient:
    def __init__(
        self,
        *,
        token_provider: FeishuTokenProvider | None = None,
        api_base: str | None = None,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = 1024 * 1024,
        max_retries: int = 3,
    ) -> None:
        self.api_base = (api_base or settings.feishu_api_base).rstrip("/")
        _validate_feishu_api_base(self.api_base)
        self.token_provider = token_provider
        self.max_response_bytes = max_response_bytes
        self.max_retries = max_retries
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=10,
            trust_env=False,
            follow_redirects=False,
        )

    async def get_tenant_access_token(self) -> str:
        if self.token_provider is None:
            self.token_provider = FeishuTokenProvider(client=self.client)
            self._owns_client = False
        return await self.token_provider.get_token()

    async def send_message_to_chat(
        self, chat_id: str, msg_type: str, content: dict[str, Any] | str
    ) -> FeishuSendResult:
        self._validate_chat_id(chat_id)
        token = await self.get_tenant_access_token()
        payload = {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": (
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            ),
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        result = await self._post_json(
            f"{self.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id",
            payload,
            headers=headers,
            invalidate_token_on_auth_error=True,
        )
        feishu_send_total.labels(provider="feishu_app", result=result_class(result)).inc()
        return result

    async def send_interactive_card(self, chat_id: str, card: FeishuCard) -> FeishuSendResult:
        return await self.send_message_to_chat(chat_id, "interactive", card)

    async def update_message_card(self, message_id: str, card: FeishuCard) -> FeishuSendResult:
        token = await self.get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        return await self._post_json(
            f"{self.api_base}/open-apis/im/v1/messages/{message_id}/update",
            {"content": json.dumps(card, ensure_ascii=False)},
            headers=headers,
            invalidate_token_on_auth_error=True,
        )

    async def send_custom_webhook(
        self,
        webhook_url: str,
        payload: dict[str, Any],
        *,
        signing_secret: str | None = None,
    ) -> FeishuSendResult:
        self._validate_webhook_url(webhook_url)
        body = payload.copy()
        if signing_secret:
            import time

            timestamp = int(time.time())
            body["timestamp"] = str(timestamp)
            body["sign"] = sign_custom_webhook(timestamp, signing_secret)
        result = await self._post_json(
            webhook_url,
            body,
            headers={"Content-Type": "application/json"},
        )
        feishu_send_total.labels(provider="feishu_webhook", result=result_class(result)).inc()
        return result

    async def health_check(self, webhook_url: str | None = None) -> bool:
        if webhook_url:
            self._validate_webhook_url(webhook_url)
            return True
        try:
            await self.get_tenant_access_token()
        except FeishuAuthenticationError:
            return False
        return True

    async def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
        invalidate_token_on_auth_error: bool = False,
    ) -> FeishuSendResult:
        target = _validate_feishu_request_url(url)
        current_url = target.url
        for attempt in range(1, self.max_retries + 2):
            started = asyncio.get_running_loop().time()
            response = await self.client.post(current_url, json=payload, headers=headers)
            response_body = await response.aread()
            if len(response_body) > self.max_response_bytes:
                raise FeishuTransientError(
                    "Feishu response exceeded maximum size", status_code=response.status_code
                )
            if 300 <= response.status_code < 400 and response.headers.get("location"):
                current_url = urljoin(current_url, response.headers["location"])
                _validate_feishu_request_url(current_url)
                continue
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            elapsed = asyncio.get_running_loop().time() - started
            status_class = response.status_code // 100
            if 200 <= response.status_code < 300:
                feishu_send_duration_seconds.labels(
                    provider="feishu",
                    result="success",
                ).observe(elapsed)
                return _send_result(response.status_code, response_body)
            if (
                response.status_code in {401, 403}
                and invalidate_token_on_auth_error
                and attempt == 1
            ):
                if self.token_provider is not None:
                    await self.token_provider.invalidate()
                headers = {
                    **headers,
                    "Authorization": f"Bearer {await self.get_tenant_access_token()}",
                }
                continue
            if response.status_code in TRANSIENT_STATUS_CODES and attempt <= self.max_retries:
                delay = retry_after if retry_after is not None else exponential_backoff(attempt)
                await asyncio.sleep(delay + random.uniform(0, 0.2))
                continue
            if status_class == 4:
                feishu_send_duration_seconds.labels(
                    provider="feishu",
                    result="permanent_error",
                ).observe(elapsed)
                return FeishuSendResult(
                    ok=False,
                    status_code=response.status_code,
                    retry_after=int(retry_after) if retry_after is not None else None,
                    error=f"Feishu HTTP {response.status_code}",
                )
            feishu_send_duration_seconds.labels(
                provider="feishu",
                result="transient_error",
            ).observe(elapsed)
            return FeishuSendResult(
                ok=False,
                status_code=response.status_code,
                retry_after=int(retry_after) if retry_after is not None else None,
                error=f"Feishu HTTP {response.status_code}",
            )
        raise FeishuTransientError("Feishu retry loop exhausted")

    def _validate_chat_id(self, chat_id: str) -> None:
        if settings.feishu_allowed_chat_ids and chat_id not in settings.feishu_allowed_chat_ids:
            raise FeishuPermanentError("Feishu chat_id is not allowlisted")

    def _validate_webhook_url(self, webhook_url: str) -> None:
        validate_feishu_webhook_url(webhook_url)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _send_result(status_code: int, body: bytes) -> FeishuSendResult:
    message_id = None
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
        message_id = (
            data.get("data", {}).get("message_id")
            or data.get("data", {}).get("message", {}).get("message_id")
            or data.get("message_id")
        )
    except json.JSONDecodeError:
        message_id = None
    return FeishuSendResult(ok=True, message_id=message_id, status_code=status_code)


def result_class(result: FeishuSendResult) -> str:
    if result.dry_run:
        return "dry_run"
    if result.ok:
        return "success"
    if result.status_code is None:
        return "network_error"
    return f"{result.status_code // 100}xx"


def validate_feishu_webhook_url(webhook_url: str) -> None:
    parsed = urlparse(webhook_url)
    if parsed.scheme != "https":
        raise FeishuConfigurationError("custom Feishu webhook URL must use HTTPS")
    host = (parsed.hostname or "").lower()
    allowed = (
        host == "open.feishu.cn"
        or host.endswith(".feishu.cn")
        or host.endswith(".larksuite.com")
    )
    if not allowed:
        raise FeishuConfigurationError("custom Feishu webhook URL host is not allowed")
    validate_public_http_url(webhook_url, resolve_dns=settings.http_validate_dns_rebinding)


def _acceptance_mock_feishu_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(
        settings.acceptance_mock_http_allowed
        and parsed.scheme == "http"
        and (parsed.hostname or "").lower() == "mock-feishu"
    )


def _validate_feishu_api_base(api_base: str) -> None:
    if _acceptance_mock_feishu_url(api_base):
        validate_public_http_url(api_base, resolve_dns=False)
        return
    if not api_base.startswith("https://"):
        raise FeishuConfigurationError("Feishu API Base must use HTTPS")
    validate_public_http_url(api_base, resolve_dns=False)


def _validate_feishu_request_url(url: str):
    if _acceptance_mock_feishu_url(url):
        return validate_public_http_url(url, resolve_dns=False)
    return validate_public_http_url(url, resolve_dns=settings.http_validate_dns_rebinding)
