from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from app.core.url_security import validate_public_http_url
from app.db.models import Event
from app.publishers.base import PublisherResult, format_event_message


class WebhookPublisher:
    channel = "webhook"

    def __init__(
        self,
        url: str,
        *,
        secret: str | None = None,
        allow_private_networks: bool = False,
        allow_localhost: bool = False,
        validate_dns_rebinding: bool = True,
        client: httpx.AsyncClient | None = None,
    ):
        validate_public_http_url(
            url,
            allow_private_networks=allow_private_networks,
            allow_localhost=allow_localhost,
            resolve_dns=validate_dns_rebinding,
        )
        self.target = url
        self.secret = secret
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=10, trust_env=False)

    async def publish(self, event: Event) -> PublisherResult:
        payload = {
            "event_id": event.id,
            "event_key": event.event_key,
            "title": event.title,
            "category": event.category,
            "status": event.status,
            "severity": event.severity,
            "trust_score": event.trust_score,
            "confirmation_count": event.confirmation_count,
            "symbols": event.symbols,
            "url": event.primary_url,
            "message": format_event_message(event),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Webhook-Signature"] = hmac.new(
                self.secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
        response = await self.client.post(self.target, content=body, headers=headers)
        if 200 <= response.status_code < 300:
            return PublisherResult(ok=True, external_id=response.headers.get("x-request-id"))
        return PublisherResult(ok=False, error=f"webhook HTTP {response.status_code}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
