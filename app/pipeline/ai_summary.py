from __future__ import annotations

from app.schemas.normalized_item import NormalizedItem

SYSTEM_PROMPT = (
    "旧版抓取期 AI 摘要已经废弃。请使用 app.integrations.ai.AIService "
    "和加密的 DeepSeek Provider 配置。"
)


async def summarize_item(
    item: NormalizedItem,
    *,
    client: object | None = None,
) -> str | None:
    _ = client
    return item.summary


async def summarize_items(items: list[NormalizedItem]) -> list[NormalizedItem]:
    return items
