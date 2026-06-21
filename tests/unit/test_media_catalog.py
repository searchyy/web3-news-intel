from __future__ import annotations

from pathlib import Path

import yaml


def _catalog() -> dict:
    return yaml.safe_load(Path("source_catalog/media.yaml").read_text(encoding="utf-8"))


def test_media_catalog_contains_required_candidates() -> None:
    sources = _catalog()["sources"]
    expected = {
        "blockbeats_newsflash",
        "foresight_news",
        "panews_news",
        "odaily_newsflash",
        "chaincatcher_news",
        "techflow_news",
        "jinse_news",
        "coindesk_rss",
        "theblock_rss",
        "decrypt_rss",
        "cointelegraph_rss",
    }
    assert expected <= set(sources)


def test_media_catalog_defaults_are_safe_for_copyright_and_confirmation() -> None:
    catalog = _catalog()
    assert catalog["policy"]["copyright_mode"] == "metadata_summary_link_only"
    for key, source in catalog["sources"].items():
        assert source["official"] is False, key
        assert source["source_group"] in {"media_zh", "media_en"}, key
        assert source["trust_score"] < 90, key
        assert source["parser_version"], key
        assert "full_article_body" in catalog["policy"]["disallowed_fields"]
        if not source["enabled"]:
            assert source["last_canary_error"], key
