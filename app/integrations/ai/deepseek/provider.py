from __future__ import annotations

import httpx

from app.integrations.ai.base import (
    AIChatResult,
    AIMessage,
    AIModelInfo,
    AIProviderRuntimeConfig,
)
from app.integrations.ai.deepseek.client import DeepSeekClient


class DeepSeekProvider:
    provider = "deepseek"

    def __init__(
        self,
        config: AIProviderRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.client = DeepSeekClient(
            api_base=config.api_base,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            client=client,
        )

    async def list_models(self) -> list[AIModelInfo]:
        return await self.client.list_models()

    async def chat_completion(self, messages: list[AIMessage]) -> AIChatResult:
        return await self.client.chat_completion(
            model=self.config.model,
            messages=messages,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            thinking_enabled=self.config.thinking_enabled,
        )
