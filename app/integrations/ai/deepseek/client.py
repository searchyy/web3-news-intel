from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.integrations.ai.base import AIChatResult, AIMessage, AIModelInfo
from app.integrations.ai.deepseek.errors import (
    AIAuthenticationError,
    AIProviderError,
    AIRateLimitedError,
    AITimeoutError,
    AITransientError,
)
from app.integrations.ai.deepseek.models import DeepSeekChatResponse, DeepSeekModelsResponse


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def list_models(self) -> list[AIModelInfo]:
        response = await self._request("GET", "/models")
        payload = DeepSeekModelsResponse.model_validate(response.json())
        return [
            AIModelInfo(
                id=item.id,
                owned_by=item.owned_by,
                metadata=item.model_dump(exclude={"id", "owned_by"}, exclude_none=True),
            )
            for item in payload.data
        ]

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[AIMessage],
        max_tokens: int,
        temperature: float,
        thinking_enabled: bool,
    ) -> AIChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if thinking_enabled:
            body["thinking"] = {"enabled": True}
        started = time.perf_counter()
        response = await self._request("POST", "/chat/completions", json=body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        payload = DeepSeekChatResponse.model_validate(response.json())
        content = ""
        if payload.choices and payload.choices[0].message is not None:
            content = payload.choices[0].message.content
        if not content:
            raise AIProviderError("AI provider returned an empty response")
        return AIChatResult(
            content=content,
            model=payload.model or model,
            prompt_tokens=payload.usage.prompt_tokens,
            completion_tokens=payload.usage.completion_tokens,
            total_tokens=payload.usage.total_tokens,
            latency_ms=latency_ms,
            raw_metadata={"response_id": payload.id},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )
        close_client = self._client is None
        try:
            response = await client.request(
                method,
                f"{self.api_base}{path}",
                json=json,
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise AITimeoutError("AI provider request timed out") from exc
        except httpx.TransportError as exc:
            raise AITransientError("AI provider network error") from exc
        finally:
            if close_client:
                await client.aclose()
        if response.status_code in {401, 403}:
            raise AIAuthenticationError("AI provider authentication failed")
        if response.status_code == 429:
            raise AIRateLimitedError(retry_after_seconds=_retry_after(response))
        if 500 <= response.status_code <= 599:
            raise AITransientError(f"AI provider HTTP {response.status_code}")
        if response.status_code >= 400:
            raise AIProviderError(f"AI provider HTTP {response.status_code}")
        return response


def _retry_after(response: httpx.Response) -> int | None:
    try:
        return int(response.headers.get("retry-after", ""))
    except ValueError:
        return None
