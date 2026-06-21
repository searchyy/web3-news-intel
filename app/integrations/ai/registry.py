from __future__ import annotations

from collections.abc import Callable

import httpx

from app.integrations.ai.base import AIProvider, AIProviderRuntimeConfig
from app.integrations.ai.deepseek.provider import DeepSeekProvider

ProviderFactory = Callable[[AIProviderRuntimeConfig, httpx.AsyncClient | None], AIProvider]


class AIProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, provider: str, factory: ProviderFactory) -> None:
        self._factories[provider] = factory

    def create(
        self,
        config: AIProviderRuntimeConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> AIProvider:
        try:
            factory = self._factories[config.provider]
        except KeyError as exc:
            raise ValueError(f"unsupported AI provider: {config.provider}") from exc
        return factory(config, client)


registry = AIProviderRegistry()


def _create_deepseek_provider(
    config: AIProviderRuntimeConfig,
    client: httpx.AsyncClient | None = None,
) -> AIProvider:
    return DeepSeekProvider(config, client=client)


registry.register("deepseek", _create_deepseek_provider)
