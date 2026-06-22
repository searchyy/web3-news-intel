from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

SUPPORTED_EXCHANGE_CATEGORIES: tuple[str, ...] = (
    "listing",
    "delisting",
    "derivatives_listing",
    "derivatives_delisting",
    "wallet_maintenance",
    "deposit_withdrawal",
    "system_maintenance",
    "security_incident",
    "trading_rule",
    "product",
    "regulatory",
)

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "security_incident",
        (
            "security incident",
            "incident",
            "exploit",
            "hack",
            "stolen",
            "compromised",
            "安全事件",
            "攻击",
            "被盗",
        ),
    ),
    (
        "derivatives_delisting",
        (
            "delist futures",
            "delisting futures",
            "perpetual contract delisting",
            "futures delisting",
            "合约下架",
            "下架合约",
        ),
    ),
    (
        "derivatives_listing",
        (
            "futures listing",
            "launch futures",
            "perpetual contract",
            "usdt perpetual",
            "合约上线",
            "上线合约",
            "永续合约",
        ),
    ),
    (
        "delisting",
        (
            "delist",
            "delisting",
            "remove trading pairs",
            "will remove",
            "下架",
            "停止交易",
        ),
    ),
    (
        "listing",
        (
            "new listing",
            "will list",
            "is now available",
            "available on",
            "listing",
            "launchpool",
            "新增交易",
            "上线",
            "上币",
        ),
    ),
    (
        "deposit_withdrawal",
        (
            "deposit",
            "withdrawal",
            "withdrawals",
            "suspend deposits",
            "suspend withdrawals",
            "充币",
            "提币",
            "充提",
            "暂停充值",
            "暂停提现",
        ),
    ),
    (
        "wallet_maintenance",
        (
            "wallet maintenance",
            "network maintenance",
            "wallet upgrade",
            "钱包维护",
            "网络维护",
        ),
    ),
    (
        "system_maintenance",
        (
            "system maintenance",
            "scheduled maintenance",
            "maintenance",
            "系统维护",
            "维护升级",
        ),
    ),
    (
        "trading_rule",
        (
            "trading rules",
            "tick size",
            "lot size",
            "fee update",
            "交易规则",
            "费率",
            "最小价格精度",
        ),
    ),
    (
        "regulatory",
        (
            "regulatory",
            "jurisdiction",
            "restricted",
            "compliance",
            "监管",
            "限制",
            "合规",
        ),
    ),
)

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = TAG_RE.sub(" ", html.unescape(str(value)))
    text = " ".join(text.split())
    return text or None


def first_value(entry: dict[str, Any], fields: list[str] | tuple[str, ...]) -> Any:
    for field in fields:
        value = resolve_path(entry, field)
        if value not in (None, ""):
            return value
    return None


def resolve_path(data: Any, path: str | None) -> Any:
    if not path:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def parse_exchange_datetime(value: Any, *, unit: str | None = None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        divisor = 1000 if unit == "milliseconds" else 1
        try:
            return datetime.fromtimestamp(float(value) / divisor, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip().isdigit() and unit in {"milliseconds", "seconds"}:
        number = int(value.strip())
        divisor = 1000 if unit == "milliseconds" else 1
        try:
            return datetime.fromtimestamp(number / divisor, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    return parse_datetime(value)


def classify_exchange_category(
    title: str,
    summary: str | None,
    default_category: str,
) -> str:
    text = f"{title} {summary or ''}".lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    if default_category in SUPPORTED_EXCHANGE_CATEGORIES:
        return default_category
    return "product"


def max_items_for_parse(source: SourceConfig, raw: RawDocumentPayload) -> int:
    configured = (
        source.config.get("max_items_per_fetch")
        or source.config.get("max_items")
        or source.config.get("live_canary_max_items")
        or 20
    )
    try:
        limit = int(configured)
    except (TypeError, ValueError):
        limit = 20
    limit = max(0, min(limit, 100))
    if raw.metadata.get("canary") or source.config.get("live_canary"):
        limit = min(limit, 10)
    return limit


def stable_content_hash(
    *,
    source_key: str,
    title: str,
    url: str,
    published_at: datetime | None,
    summary: str | None,
) -> str:
    parts = [
        source_key,
        clean_text(title) or "",
        canonicalize_url(url),
        published_at.isoformat() if published_at else "",
        clean_text(summary) or "",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def build_normalized_item(
    *,
    source: SourceConfig,
    raw: RawDocumentPayload,
    title: Any,
    url: Any,
    summary: Any = None,
    published_at: datetime | None = None,
    item_id: Any = None,
    parser_name: str,
    parser_version: str,
    category: str | None = None,
    extra_raw: dict[str, Any] | None = None,
) -> NormalizedItem | None:
    cleaned_title = clean_text(title)
    if not cleaned_title or not url:
        return None
    absolute_url = urljoin(raw.url, str(url))
    cleaned_summary = clean_text(summary)
    exchange_category = category or classify_exchange_category(
        cleaned_title,
        cleaned_summary,
        source.category,
    )
    content_hash = stable_content_hash(
        source_key=source.key,
        title=cleaned_title,
        url=absolute_url,
        published_at=published_at,
        summary=cleaned_summary,
    )
    item_raw = {
        "source_group": source.config.get("source_group", "exchange_official"),
        "official": bool(source.config.get("official", True)),
        "parser": parser_name,
        "parser_version": parser_version,
        "content_hash": content_hash,
        "exchange_category": exchange_category,
        "item_id": str(item_id) if item_id not in (None, "") else None,
        "fetched_body_hash": raw.body_hash,
    }
    if extra_raw:
        item_raw.update(extra_raw)
    return NormalizedItem(
        title=cleaned_title,
        summary=cleaned_summary[:1000] if cleaned_summary else None,
        url=absolute_url,
        canonical_url=canonicalize_url(absolute_url),
        published_at=published_at,
        source_key=source.key,
        source_type=source.source_type,
        category=exchange_category,
        language=source.language,
        raw=item_raw,
    )


def dedupe_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    seen: set[str] = set()
    output: list[NormalizedItem] = []
    for item in items:
        key = item.canonical_url or item.url or item.raw.get("content_hash")
        if key in seen:
            continue
        seen.add(str(key))
        output.append(item)
    return output


def render_template(template: str | None, entry: dict[str, Any]) -> str | None:
    if not template:
        return None
    try:
        return str(template).format(**entry)
    except (KeyError, ValueError):
        return None
