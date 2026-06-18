from __future__ import annotations

from app.core.config import SourcesFile


def test_sources_file_injects_mapping_keys() -> None:
    parsed = SourcesFile.model_validate(
        {
            "sources": {
                "sample": {
                    "name": "Sample",
                    "source_type": "tier1_media",
                    "adapter": "rss",
                    "url": "https://example.com/rss",
                    "canonical_url": "https://example.com/rss",
                    "category": "media",
                    "timeout_seconds": 15,
                    "max_response_bytes": 2097152,
                }
            }
        }
    )
    assert parsed.sources["sample"].key == "sample"
