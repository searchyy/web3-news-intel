from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.url_security import validate_public_http_url

AdapterName = Literal["rss", "json_api", "graphql", "html"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "web3-news-intel"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://web3_news:web3_news@localhost:5432/web3_news_intel"
    redis_url: str = "redis://localhost:6379/0"
    sources_file: str = "sources.yaml"
    admin_token: str | None = None
    cors_allowed_origins: list[str] = Field(default_factory=list)
    allow_private_networks: bool = Field(
        default=False,
        validation_alias=AliasChoices("HTTP_ALLOW_PRIVATE_NETWORKS", "ALLOW_PRIVATE_NETWORKS"),
    )
    http_allow_localhost: bool = False
    http_validate_dns_rebinding: bool = True

    http_timeout_seconds: float = 15.0
    http_max_response_bytes: int = 2 * 1024 * 1024
    http_per_host_rate_limit_seconds: float = 1.0
    http_max_retries: int = 3
    http_max_redirects: int = 5
    http_user_agent: str = "web3-news-intel/0.1 (+https://example.invalid/compliance)"

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    alert_webhook_url: str | None = None
    alert_webhook_secret: str | None = None
    enable_acceptance_tasks: bool = False
    celery_redis_visibility_timeout_seconds: int = Field(default=3600, ge=1, le=86400)


settings = Settings()


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    source_type: str
    adapter: AdapterName
    url: str
    canonical_url: str
    category: str
    language: str | None = None
    trust_score: int = Field(default=50, ge=0, le=100)
    poll_seconds: int = Field(default=300, ge=30)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    enabled: bool = True
    allow_private_networks: bool = False
    allow_localhost: bool = False
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("source key must contain letters, digits, '-' or '_' only")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("source URL must be HTTP(S)")
        return value

    @model_validator(mode="after")
    def validate_urls(self) -> SourceConfig:
        resolve_dns = settings.http_validate_dns_rebinding and self.enabled
        try:
            validate_public_http_url(
                self.url,
                allow_private_networks=self.allow_private_networks,
                allow_localhost=self.allow_localhost or settings.http_allow_localhost,
                resolve_dns=resolve_dns,
            )
            validate_public_http_url(
                self.canonical_url,
                allow_private_networks=self.allow_private_networks,
                allow_localhost=self.allow_localhost or settings.http_allow_localhost,
                resolve_dns=resolve_dns,
            )
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        if self.config.get("parser") is None and self.adapter == "html":
            raise ValueError("HTML sources must declare config.parser")
        return self


class SourcesFile(BaseModel):
    sources: dict[str, SourceConfig]

    @field_validator("sources", mode="before")
    @classmethod
    def inject_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("sources must be a mapping")
        hydrated: dict[str, Any] = {}
        for key, raw in value.items():
            if not isinstance(raw, dict):
                raise ValueError(f"source {key!r} must be a mapping")
            hydrated[key] = {"key": key, **raw}
        return hydrated

    def enabled_sources(self) -> list[SourceConfig]:
        return [source for source in self.sources.values() if source.enabled]


def load_sources(path: str | Path | None = None) -> SourcesFile:
    source_path = Path(path or settings.sources_file)
    with source_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return SourcesFile.model_validate(data)
