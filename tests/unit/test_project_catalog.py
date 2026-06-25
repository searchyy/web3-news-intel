from __future__ import annotations

from pathlib import Path

import yaml


def _catalog() -> dict:
    return yaml.safe_load(Path("source_catalog/projects.yaml").read_text(encoding="utf-8"))


def test_project_catalog_contains_requested_sources() -> None:
    sources = _catalog()["sources"]
    assert {
        "aster_medium",
        "aster_product_releases",
        "aster_api_docs_commits",
        "hyperliquid_telegram_announcements",
        "backpack_blog",
        "backpack_status",
        "hyperliquid_news_search",
        "aster_news_search",
        "backpack_news_search",
    } <= set(sources)



def test_project_official_sources_are_enabled() -> None:
    sources = _catalog()["sources"]
    for key in (
        "aster_medium",
        "aster_product_releases",
        "aster_api_docs_commits",
        "hyperliquid_telegram_announcements",
        "backpack_blog",
        "backpack_status",
    ):
        source = sources[key]
        assert source["enabled"] is True
        assert source["official"] is True
        assert source["source_group"] == "project_official"



def test_project_news_aggregators_are_enabled_by_operator_request() -> None:
    sources = _catalog()["sources"]
    for key in ("hyperliquid_news_search", "aster_news_search", "backpack_news_search"):
        source = sources[key]
        assert source["enabled"] is True
        assert source["source_group"] == "project_news"
        assert source["official"] is False
        assert source["live_canary_status"] == "NOT_RUN"