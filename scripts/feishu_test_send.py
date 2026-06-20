from __future__ import annotations

import asyncio
import json
import sys

from app.core.config import settings
from app.integrations.feishu.client import FeishuClient


async def _main() -> int:
    if not settings.feishu_test_chat_id:
        print("FEISHU_TEST_CHAT_ID is required", file=sys.stderr)
        return 2
    if not settings.feishu_enabled or not settings.feishu_send_enabled:
        print("FEISHU_ENABLED and FEISHU_SEND_ENABLED must be true", file=sys.stderr)
        return 2
    client = FeishuClient()
    try:
        result = await client.send_interactive_card(
            settings.feishu_test_chat_id,
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": "web3-news-intel test card"},
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "This is a single manually dispatched Feishu test card.",
                        },
                    }
                ],
            },
        )
    finally:
        await client.aclose()
    print(
        json.dumps(
            {
                "ok": result.ok,
                "status_code": result.status_code,
                "message_id_present": bool(result.message_id),
                "error": result.error,
            },
            sort_keys=True,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
