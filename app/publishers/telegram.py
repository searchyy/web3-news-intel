from __future__ import annotations

import httpx

from app.db.models import Event
from app.publishers.base import PublisherResult, format_event_message


class TelegramPublisher:
    channel = "telegram"

    def __init__(self, *, bot_token: str, chat_id: str, client: httpx.AsyncClient | None = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.target = chat_id
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=10, trust_env=False)

    async def publish(self, event: Event) -> PublisherResult:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = await self.client.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": format_event_message(event),
                "disable_web_page_preview": False,
            },
        )
        if 200 <= response.status_code < 300:
            payload = response.json()
            return PublisherResult(
                ok=True, external_id=str(payload.get("result", {}).get("message_id"))
            )
        return PublisherResult(ok=False, error=f"telegram HTTP {response.status_code}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
