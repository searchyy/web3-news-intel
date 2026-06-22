from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AIMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AIModelInfo:
    id: str
    owned_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AIChatResult:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AIProviderRuntimeConfig:
    provider: str
    api_base: str
    api_key: str
    model: str
    timeout_seconds: int
    max_tokens: int
    temperature: float
    thinking_enabled: bool = False


class AIProvider(Protocol):
    provider: str

    async def list_models(self) -> list[AIModelInfo]:
        raise NotImplementedError

    async def chat_completion(self, messages: list[AIMessage]) -> AIChatResult:
        raise NotImplementedError
