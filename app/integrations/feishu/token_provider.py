from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import httpx
import redis.asyncio as redis_async

from app.core.config import settings
from app.integrations.feishu.errors import FeishuAuthenticationError, FeishuConfigurationError
from app.observability.metrics import feishu_token_refresh_total


class FeishuTokenProvider:
    def __init__(
        self,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_base: str | None = None,
        redis_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.app_id = app_id or settings.feishu_app_id
        self.app_secret = app_secret or settings.feishu_app_secret
        self.api_base = (api_base or settings.feishu_api_base).rstrip("/")
        if not self.app_id or not self.app_secret:
            raise FeishuConfigurationError("Feishu app_id/app_secret are not configured")
        app_hash = hashlib.sha256(self.app_id.encode("utf-8")).hexdigest()[:16]
        self.cache_key = f"feishu:tenant-token:{app_hash}"
        self.lock_key = f"feishu:tenant-token-lock:{app_hash}"
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=10, trust_env=False)
        self.redis = redis_client or redis_async.Redis.from_url(redis_url or settings.redis_url)

    async def get_token(self) -> str:
        cached = await self.redis.get(self.cache_key)
        if cached:
            return cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
        return await self.refresh_token()

    async def refresh_token(self) -> str:
        lock_value = str(time.time())
        acquired = await self.redis.set(self.lock_key, lock_value, nx=True, ex=30)
        if not acquired:
            for _ in range(30):
                await asyncio.sleep(0.2)
                cached = await self.redis.get(self.cache_key)
                if cached:
                    return cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
            raise FeishuAuthenticationError("timed out waiting for Feishu token refresh")
        try:
            payload = {"app_id": self.app_id, "app_secret": self.app_secret}
            response = await self.client.post(
                f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
                json=payload,
            )
            if response.status_code >= 400:
                raise FeishuAuthenticationError(
                    f"Feishu token endpoint HTTP {response.status_code}"
                )
            data = response.json()
            token = data.get("tenant_access_token")
            expire = int(data.get("expire") or 0)
            if not token or expire <= 0:
                raise FeishuAuthenticationError("Feishu token response was malformed")
            ttl = max(1, expire - 300)
            await self.redis.set(self.cache_key, token, ex=ttl)
            feishu_token_refresh_total.labels(result="success").inc()
            return str(token)
        except Exception:
            feishu_token_refresh_total.labels(result="failure").inc()
            raise
        finally:
            await self.redis.delete(self.lock_key)

    async def invalidate(self) -> None:
        await self.redis.delete(self.cache_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
