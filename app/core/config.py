from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.url_security import validate_public_http_url

AdapterName = Literal[
    "rss",
    "json_api",
    "graphql",
    "html",
    "exchange_rss",
    "exchange_json",
    "exchange_html",
    "media_rss",
    "media_html",
    "media_json_api",
    "okx_help_app_state",
]


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
    http_validate_source_dns_on_load: bool = False
    http_trust_env: bool = False

    http_timeout_seconds: float = 15.0
    http_max_response_bytes: int = 2 * 1024 * 1024
    http_per_host_rate_limit_seconds: float = 1.0
    http_max_retries: int = 3
    http_max_redirects: int = 5
    http_user_agent: str = "web3-news-intel/0.1 (+https://example.invalid/compliance)"
    ai_enabled: bool = False
    ai_auto_process_enabled: bool = False
    ai_provider: str = "deepseek"
    ai_execution_mode: Literal["sync", "async"] = "async"
    ai_sync_allowed_environments: str = "local,development,test"
    ai_sync_timeout_seconds: int = Field(default=60, ge=1, le=240)
    ai_queue_name: str = "ai"
    ai_job_stuck_seconds: int = Field(default=10, ge=1, le=300)
    ai_job_timeout_seconds: int = Field(default=90, ge=5, le=600)
    ai_default_mode: Literal["fast", "deep"] = "fast"
    deepseek_api_base: str = "https://api.deepseek.com"
    deepseek_request_timeout_seconds: int = Field(default=90, ge=1, le=300)
    deepseek_max_concurrency: int = Field(default=2, ge=1, le=10)
    deepseek_daily_token_budget: int = Field(default=0, ge=0)
    deepseek_allow_custom_api_base: bool = False
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

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
    feishu_allowed_chat_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    feishu_default_delivery_mode: Literal["immediate", "digest"] = "immediate"
    feishu_max_messages_per_group_per_hour: int = Field(default=30, ge=1, le=500)
    field_encryption_key: str | None = None
    public_base_url: str | None = None
    admin_username: str = "admin"
    admin_password_hash: str | None = None
    admin_session_secret: str | None = None
    admin_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    admin_secure_cookie: bool = False
    enable_acceptance_tasks: bool = False
    acceptance_mock_http_enabled: bool = False
    celery_redis_visibility_timeout_seconds: int = Field(default=3600, ge=1, le=86400)

    @field_validator("feishu_allowed_chat_ids", mode="before")
    @classmethod
    def parse_chat_allowlist(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError("FEISHU_ALLOWED_CHAT_IDS JSON array is invalid") from exc
                if not isinstance(parsed, list):
                    raise ValueError("FEISHU_ALLOWED_CHAT_IDS JSON value must be a list")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
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
            if self.acceptance_mock_http_allowed:
                validate_public_http_url(
                    self.feishu_api_base,
                    allow_private_networks=False,
                    allow_localhost=False,
                    resolve_dns=False,
                )
            else:
                validate_public_http_url(
                    self.feishu_api_base,
                    allow_private_networks=False,
                    allow_localhost=False,
                    resolve_dns=False,
                )
                if not self.feishu_api_base.startswith("https://"):
                    raise ValueError("FEISHU_API_BASE must use HTTPS")
        if self.deepseek_api_base:
            if self.acceptance_mock_http_allowed and self.deepseek_api_base.startswith(
                "http://mock-deepseek"
            ):
                validate_public_http_url(
                    self.deepseek_api_base,
                    allow_private_networks=False,
                    allow_localhost=False,
                    resolve_dns=False,
                )
            else:
                validate_public_http_url(
                    self.deepseek_api_base,
                    allow_private_networks=False,
                    allow_localhost=False,
                    resolve_dns=self.deepseek_allow_custom_api_base,
                )
                if (
                    self.deepseek_api_base.rstrip("/") != "https://api.deepseek.com"
                    and not self.deepseek_allow_custom_api_base
                ):
                    raise ValueError(
                        "DEEPSEEK_ALLOW_CUSTOM_API_BASE must be true for non-official API base"
                    )
        if self.app_env.lower() == "production":
            if not self.admin_secure_cookie:
                raise ValueError("ADMIN_SECURE_COOKIE must be true in production")
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

    @property
    def acceptance_mock_http_allowed(self) -> bool:
        return (
            self.acceptance_mock_http_enabled
            and self.enable_acceptance_tasks
            and self.app_env.lower() in {"test", "ci"}
        )


settings = Settings()


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    display_name_zh: str | None = None
    source_group: str = "legacy"
    source_type: str
    adapter: AdapterName
    url: str
    canonical_url: str
    category: str
    language: str | None = None
    official: bool = False
    trust_score: int = Field(default=50, ge=0, le=100)
    poll_seconds: int = Field(default=300, ge=30)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_response_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        le=10 * 1024 * 1024,
        validation_alias=AliasChoices("max_response_bytes", "maximum_response_bytes"),
    )
    max_items_per_fetch: int = Field(default=50, ge=1, le=1000)
    enabled: bool = True
    allow_private_networks: bool = False
    allow_localhost: bool = False
    ranking_provider: str | None = None
    ranking_position: int | None = Field(default=None, ge=1)
    ranking_snapshot_at: datetime | None = None
    parser_version: str = "v1"
    supported_categories: list[str] = Field(default_factory=list)
    health_status: str = "unknown"
    live_canary_status: str = "unknown"
    last_canary_at: datetime | None = None
    last_canary_error: str | None = None
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
        resolve_dns = settings.http_validate_source_dns_on_load and self.enabled
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
        if self.config.get("parser") is None and (
            self.adapter == "html" or self.adapter.endswith("_html")
        ):
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


def load_source_catalog_dir(path: str | Path = "source_catalog") -> dict[str, SourceConfig]:
    catalog_dir = Path(path)
    if not catalog_dir.exists():
        return {}
    sources: dict[str, SourceConfig] = {}
    for catalog_path in sorted(catalog_dir.glob("*.yaml")):
        with catalog_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, dict):
            continue
        for key, raw in raw_sources.items():
            if isinstance(raw, dict):
                sources[str(key)] = _source_config_from_catalog(str(key), raw)
    return sources


def load_runtime_sources(
    sources_path: str | Path | None = None,
    catalog_dir: str | Path = "source_catalog",
) -> dict[str, SourceConfig]:
    merged = dict(load_sources(sources_path).sources)
    merged.update(load_source_catalog_dir(catalog_dir))
    return merged


def _source_config_from_catalog(key: str, raw: dict[str, Any]) -> SourceConfig:
    config = dict(raw.get("config") or {})
    parser = raw.get("parser")
    if parser and not config.get("parser"):
        config["parser"] = parser
    parser_version = raw.get("parser_version")
    if parser_version and not config.get("parser_version"):
        config["parser_version"] = parser_version
    if raw.get("max_items_per_fetch") and not config.get("max_items"):
        config["max_items"] = raw["max_items_per_fetch"]
    payload = {
        "key": key,
        "name": raw["name"],
        "display_name_zh": raw.get("display_name_zh"),
        "source_group": raw.get("source_group", "legacy"),
        "source_type": raw["source_type"],
        "adapter": raw["adapter"],
        "url": raw["url"],
        "canonical_url": raw["canonical_url"],
        "category": raw["category"],
        "language": raw.get("language"),
        "official": bool(raw.get("official", False)),
        "trust_score": raw.get("trust_score", 50),
        "poll_seconds": raw.get("poll_seconds", 300),
        "timeout_seconds": raw.get("timeout_seconds", 15),
        "max_response_bytes": raw.get(
            "max_response_bytes",
            raw.get("maximum_response_bytes", 2 * 1024 * 1024),
        ),
        "max_items_per_fetch": raw.get("max_items_per_fetch", 50),
        "enabled": bool(raw.get("enabled", False)),
        "allow_private_networks": bool(raw.get("allow_private_networks", False)),
        "allow_localhost": bool(raw.get("allow_localhost", False)),
        "ranking_provider": raw.get("ranking_provider"),
        "ranking_position": raw.get("ranking_position"),
        "ranking_snapshot_at": raw.get("ranking_snapshot_at"),
        "parser_version": raw.get("parser_version", "v1"),
        "supported_categories": raw.get("supported_categories") or [],
        "health_status": raw.get("health_status", "unknown"),
        "live_canary_status": raw.get("live_canary_status", "unknown"),
        "last_canary_at": raw.get("last_canary_at"),
        "last_canary_error": raw.get("last_canary_error"),
        "config": config,
    }
    return SourceConfig.model_validate(payload)
