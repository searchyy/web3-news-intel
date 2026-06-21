from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.url_security import validate_public_http_url

DEEPSEEK_OFFICIAL_API_BASE = "https://api.deepseek.com"


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    ai_auto_process_enabled: bool = Field(default=False, alias="AI_AUTO_PROCESS_ENABLED")
    ai_provider: str = Field(default="deepseek", alias="AI_PROVIDER")
    ai_allow_custom_api_base: bool = Field(default=False, alias="AI_ALLOW_CUSTOM_API_BASE")
    deepseek_api_base: str = Field(default=DEEPSEEK_OFFICIAL_API_BASE, alias="DEEPSEEK_API_BASE")
    deepseek_request_timeout_seconds: int = Field(
        default=90,
        ge=1,
        le=300,
        alias="DEEPSEEK_REQUEST_TIMEOUT_SECONDS",
    )
    deepseek_max_concurrency: int = Field(
        default=2,
        ge=1,
        le=20,
        alias="DEEPSEEK_MAX_CONCURRENCY",
    )
    deepseek_daily_token_budget: int = Field(
        default=0,
        ge=0,
        alias="DEEPSEEK_DAILY_TOKEN_BUDGET",
    )

    @field_validator("ai_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "deepseek":
            raise ValueError("AI_PROVIDER 当前仅支持 deepseek")
        return normalized

    @model_validator(mode="after")
    def validate_api_base(self) -> AISettings:
        validate_deepseek_api_base(
            self.deepseek_api_base,
            allow_custom=self.ai_allow_custom_api_base,
        )
        return self


def validate_deepseek_api_base(value: str, *, allow_custom: bool) -> str:
    base = value.rstrip("/")
    if not allow_custom and base != DEEPSEEK_OFFICIAL_API_BASE:
        raise ValueError("DeepSeek API Base 默认固定为官方地址")
    if not base.startswith("https://"):
        raise ValueError("DeepSeek API Base 必须使用 HTTPS")
    validate_public_http_url(
        base,
        allow_private_networks=False,
        allow_localhost=False,
        resolve_dns=allow_custom,
    )
    return base


ai_settings = AISettings()
