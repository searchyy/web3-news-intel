from __future__ import annotations

import html
import re
from typing import Any

from app.pipeline.entities import extract_chains, extract_symbols

SUMMARY_MAX_CHARS = 700

MEDIA_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "newsflash": ("快讯", "newsflash", "flash", "breaking", "just in"),
    "deep_article": ("深度", "long read", "analysis", "opinion", "feature", "专访"),
    "market": ("market", "price", "涨", "跌", "行情", "etf", "期货", "options"),
    "fundraising": ("融资", "funding", "raises", "seed round", "series a", "投融资"),
    "policy_regulatory": (
        "监管",
        "政策",
        "sec",
        "cftc",
        "lawsuit",
        "sanction",
        "regulation",
        "法案",
    ),
    "hack_security": (
        "hack",
        "hacker",
        "exploit",
        "漏洞",
        "攻击",
        "被盗",
        "security incident",
        "drained",
    ),
    "project_update": ("mainnet", "upgrade", "更新", "升级", "roadmap", "launches"),
    "token_unlock": ("unlock", "token unlock", "解锁", "vesting"),
    "onchain": ("on-chain", "onchain", "链上", "whale", "gas", "transaction"),
    "exchange_repost": (
        "listing",
        "delisting",
        "上币",
        "下架",
        "binance",
        "okx",
        "coinbase",
        "交易所公告",
    ),
}


def clean_text(value: object, *, max_chars: int | None = None) -> str | None:
    if value in (None, ""):
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split()).strip()
    if not text:
        return None
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def classify_media_category(
    title: str,
    summary: str | None,
    tags: list[str],
    fallback: str,
) -> tuple[str, list[str]]:
    text = " ".join([title, summary or "", *tags]).lower()
    matched: list[str] = []
    for category, keywords in MEDIA_CATEGORY_KEYWORDS.items():
        if any(_keyword_matches(text, keyword.lower()) for keyword in keywords):
            matched.append(category)
    for preferred in (
        "newsflash",
        "hack_security",
        "token_unlock",
        "exchange_repost",
        "policy_regulatory",
        "fundraising",
    ):
        if preferred in matched:
            return preferred, matched
    return (matched[0] if matched else fallback), matched


def _keyword_matches(text: str, keyword: str) -> bool:
    if keyword.isascii() and keyword.replace(" ", "").isalnum() and len(keyword) <= 4:
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
    return keyword in text


def safe_media_raw_metadata(
    *,
    source_key: str,
    source_group: str,
    parser: str,
    parser_version: str,
    provider_id: object | None,
    author: str | None,
    tags: list[str],
    category: str,
    category_signals: list[str],
    title: str,
    summary: str | None,
    original_url: str,
    official_confirmation: bool = False,
) -> dict[str, Any]:
    text = f"{title} {summary or ''}"
    symbols = extract_symbols(text)
    chains = extract_chains(text)
    return {
        "provider_id": str(provider_id) if provider_id not in (None, "") else None,
        "author": author,
        "tags": tags,
        "media_category": category,
        "category_signals": category_signals,
        "source_group": source_group,
        "parser": parser,
        "parser_version": parser_version,
        "official_confirmation": official_confirmation,
        "requires_multisource_confirmation": not official_confirmation,
        "copyright_scope": "metadata_summary_link_only",
        "article_body_saved": False,
        "cluster_hint": {
            "source_key": source_key,
            "category": category,
            "symbols": symbols,
            "chains": chains,
            "original_url": original_url,
            "title_fingerprint_basis": title,
        },
    }


def media_source_group(source_config: dict[str, Any], language: str | None) -> str:
    configured = source_config.get("source_group")
    if configured:
        return str(configured)
    return "media_zh" if language == "zh" else "media_en"


def list_from_value(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]
