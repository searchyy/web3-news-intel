from __future__ import annotations

from app.pipeline.ai_summary import SYSTEM_PROMPT, summarize_item, summarize_items
from app.schemas.normalized_item import NormalizedItem


async def test_legacy_ai_summary_is_deprecated_noop() -> None:
    item = _item()
    assert await summarize_item(item, client=object()) is None


async def test_legacy_ai_summary_preserves_existing_summary() -> None:
    item = _item(summary="已有摘要")
    assert await summarize_item(item, client=object()) == "已有摘要"
    assert await summarize_items([item]) == [item]


def test_legacy_ai_summary_prompt_points_to_deepseek_service() -> None:
    assert "废弃" in SYSTEM_PROMPT
    assert "AIService" in SYSTEM_PROMPT
    assert "DeepSeek" in SYSTEM_PROMPT


def _item(summary: str | None = None) -> NormalizedItem:
    return NormalizedItem(
        title="Binance Will List Re (RE) with Seed Tag Applied",
        summary=summary,
        url="https://example.com/re",
        canonical_url="https://example.com/re",
        source_key="binance_listing",
        source_type="exchange_official",
        category="listing",
        language="en",
    )
