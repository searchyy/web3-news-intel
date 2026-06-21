from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DeepSeekModelItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    object: str | None = None
    owned_by: str | None = None


class DeepSeekModelsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    object: str | None = None
    data: list[DeepSeekModelItem] = Field(default_factory=list)


class DeepSeekChatMessage(BaseModel):
    role: str
    content: str


class DeepSeekChatChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int | None = None
    message: DeepSeekChatMessage | None = None
    finish_reason: str | None = None


class DeepSeekUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class DeepSeekChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    model: str | None = None
    choices: list[DeepSeekChatChoice] = Field(default_factory=list)
    usage: DeepSeekUsage = Field(default_factory=DeepSeekUsage)
    extra: dict[str, Any] = Field(default_factory=dict)
