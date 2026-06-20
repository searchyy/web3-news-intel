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
    feishu_enabled: bool = False
    feishu_send_enabled: bool = False
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_verification_token: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_api_base: str = "https://open.feishu.cn"
    feishu_test_chat_id: str | None = None
    feishu_allowed_chat_ids: list[str] = Field(default_factory=list)
    feishu_default_delivery_mode: Literal["immediate", "digest"] = "immediate"
    feishu_max_messages_per_group_per_hour: int = Field(default=30, ge=1, le=500)
    field_encryption_key: str | None = None
    public_base_url: str | None = None
    admin_username: str = "admin"
    admin_password_hash: str | None = None
    admin_session_secret: str | None = None
    admin_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    admin_secure_cookie: bool = True
    enable_acceptance_tasks: bool = False
    celery_redis_visibility_timeout_seconds: int = Field(default=3600, ge=1, le=86400)

    @field_validator("feishu_allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_allowlist(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("FEISHU_ALLOWED_CHAT_IDS must be a comma-separated string or list")

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.public_base_url:
            validate_public_http_url(
                self.public_base_url,
                allow_private_networks=self.allow_private_networks,
                allow_localhost=self.http_allow_localhost,
                resolve_dns=False,
            )
            if self.public_base_url.startswith("https://") and not self.admin_secure_cookie:
                raise ValueError("ADMIN_SECURE_COOKIE must be true for HTTPS PUBLIC_BASE_URL")
        if self.feishu_api_base:
            validate_public_http_url(
                self.feishu_api_base,
                allow_private_networks=False,
                allow_localhost=False,
                resolve_dns=False,
            )
        if self.app_env.lower() == "production":
            missing = []
            if not self.admin_password_hash:
                missing.append("ADMIN_PASSWORD_HASH")
            if not self.admin_session_secret:
                missing.append("ADMIN_SESSION_SECRET")
            if self.feishu_send_enabled:
                for name, value in (
                    ("FEISHU_APP_ID", self.feishu_app_id),
                    ("FEISHU_APP_SECRET", self.feishu_app_secret),
                ):
                    if not value:
                        missing.append(name)
            if self.feishu_send_enabled and not self.feishu_enabled:
                raise ValueError("FEISHU_ENABLED must be true when FEISHU_SEND_ENABLED is true")
            if missing:
                raise ValueError(f"missing production configuration: {', '.join(missing)}")
        return self


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
