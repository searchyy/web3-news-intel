from __future__ import annotations

from collections.abc import Iterable

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exploit": ("exploit", "hack", "drained", "stolen", "security incident", "vulnerability"),
    "depeg": ("depeg", "de-pegged", "lost peg", "stablecoin peg"),
    "chain_halt": ("halted", "chain halt", "network outage", "stopped producing blocks"),
    "enforcement": (
        "charges",
        "settles charges",
        "lawsuit",
        "enforcement",
        "indictment",
        "sanction",
    ),
    "delisting": ("delist", "will remove", "cease trading"),
    "listing": ("list", "listing", "new spot trading", "adds support"),
    "protocol_upgrade": ("upgrade", "hard fork", "mainnet release", "release notes"),
    "governance_passed": ("proposal passed", "vote passed", "approved proposal", "governance vote"),
    "funding": ("raises", "funding", "seed round", "series a"),
    "partnership": ("partners with", "partnership", "collaboration"),
    "rumor": ("rumor", "unconfirmed", "sources say"),
}


def detect_category(title: str, summary: str | None, fallback: str) -> str:
    text = f"{title} {summary or ''}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return fallback


def is_sensitive_category(category: str) -> bool:
    return category in {"exploit", "depeg", "chain_halt", "enforcement", "delisting"}


def is_official_source(source_type: str) -> bool:
    return source_type in {
        "regulator_official",
        "exchange_official",
        "protocol_official",
        "governance_api",
    }


def all_media_source_types(source_types: Iterable[str]) -> bool:
    media_types = {"tier1_media", "chinese_media", "aggregator", "social"}
    return all(source_type in media_types for source_type in source_types)
