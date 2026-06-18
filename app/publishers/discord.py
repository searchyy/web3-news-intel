from __future__ import annotations

import httpx

from app.core.url_security import validate_public_http_url
from app.db.models import Event
from app.publishers.base import PublisherResult, format_event_message


class DiscordPublisher:
    channel = "discord"

    def __init__(
        self,
        webhook_url: str,
        *,
        allow_private_networks: bool = False,
        allow_localhost: bool = False,
        validate_dns_rebinding: bool = True,
        client: httpx.AsyncClient | None = None,
    ):
        validate_public_http_url(
            webhook_url,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
            resolve_dns=validate_dns_rebinding,
        )
        self.target = webhook_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=10, trust_env=False)

    async def publish(self, event: Event) -> PublisherResult:
        response = await self.client.post(
            self.target,
            json={"content": format_event_message(event), "allowed_mentions": {"parse": []}},
        )
        if response.status_code in {200, 204}:
            return PublisherResult(ok=True, external_id=response.headers.get("x-request-id"))
        return PublisherResult(ok=False, error=f"discord HTTP {response.status_code}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
