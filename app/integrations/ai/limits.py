from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.integrations.ai.deepseek.errors import (
    AIBudgetExceededError,
    AICircuitOpenError,
    AIRateLimitedError,
    AITransientError,
)
from app.integrations.ai.schemas import AIUsageSnapshot


@dataclass(frozen=True, slots=True)
class AILimitLease:
    provider: str
    request_key: str
    token: str


class AILimitController:
    """Redis-backed AI limiter shared by API and Celery workers.

    Unit tests without Redis use a local fallback only when APP_ENV is test/local and no
    Redis URL is explicitly supplied. Production and CI fail closed if Redis is unavailable.
    """

    def __init__(self, *, key_prefix: str = "web3_news:ai") -> None:
        self.key_prefix = key_prefix
        self._redis: redis.Redis | None = None
        self._local = _LocalLimitController()

    @contextmanager
    def reserve(
        self,
        provider: str,
        *,
        max_concurrency: int,
        requests_per_minute: int,
        daily_request_budget: int,
        daily_token_budget: int,
    ) -> Iterator[None]:
        if self._should_use_local_fallback():
            with self._local.reserve(
                provider,
                max_concurrency=max_concurrency,
                requests_per_minute=requests_per_minute,
                daily_request_budget=daily_request_budget,
                daily_token_budget=daily_token_budget,
            ):
                yield
            return

        lease = self._acquire_redis(
            provider,
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
            daily_request_budget=daily_request_budget,
            daily_token_budget=daily_token_budget,
        )
        try:
            yield
        finally:
            self._release_redis(lease)

    @contextmanager
    def input_hash_lock(self, lock_key: str, *, ttl_seconds: int = 300) -> Iterator[None]:
        if self._should_use_local_fallback():
            with self._local.input_hash_lock(lock_key, ttl_seconds=ttl_seconds):
                yield
            return

        client = self._client()
        token = uuid4().hex
        key = f"{self.key_prefix}:dedupe:{lock_key}"
        try:
            acquired = client.set(key, token, nx=True, ex=ttl_seconds)
        except RedisError as exc:
            raise AITransientError("AI Redis dedupe lock unavailable") from exc
        if not acquired:
            raise AIRateLimitedError(retry_after_seconds=1)
        try:
            yield
        finally:
            self._release_lock(client, key, token)

    def sync_daily_usage(self, provider: str, usage: AIUsageSnapshot) -> None:
        if self._should_use_local_fallback():
            self._local.sync_daily_usage(provider, usage)
            return
        client = self._client()
        day_ttl = _seconds_until_tomorrow()
        tokens_key = self._daily_key(provider, "tokens")
        requests_key = self._daily_key(provider, "requests")
        script = """
        local token_current = tonumber(redis.call('GET', KEYS[1]) or '0')
        local request_current = tonumber(redis.call('GET', KEYS[2]) or '0')
        local token_seen = tonumber(ARGV[1])
        local request_seen = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])
        if token_seen > token_current then redis.call('SET', KEYS[1], token_seen, 'EX', ttl) end
        if request_seen > request_current then
            redis.call('SET', KEYS[2], request_seen, 'EX', ttl)
        end
        return 1
        """
        try:
            client.eval(
                script,
                2,
                tokens_key,
                requests_key,
                usage.tokens_today,
                usage.requests_today,
                day_ttl,
            )
        except RedisError as exc:
            raise AITransientError("AI Redis budget sync unavailable") from exc

    def record_token_usage(self, provider: str, tokens: int) -> None:
        if tokens <= 0:
            return
        if self._should_use_local_fallback():
            self._local.record_token_usage(provider, tokens)
            return
        client = self._client()
        key = self._daily_key(provider, "tokens")
        try:
            value = client.incrby(key, int(tokens))
            if int(value) == int(tokens):
                client.expire(key, _seconds_until_tomorrow())
        except RedisError as exc:
            raise AITransientError("AI Redis token budget update unavailable") from exc

    def record_success(self, provider: str) -> None:
        if self._should_use_local_fallback():
            self._local.record_success(provider)
            return
        client = self._client()
        try:
            client.delete(self._key(provider, "failures"), self._key(provider, "circuit_until"))
        except RedisError as exc:
            raise AITransientError("AI Redis circuit update unavailable") from exc

    def record_failure(self, provider: str) -> None:
        if self._should_use_local_fallback():
            self._local.record_failure(provider)
            return
        client = self._client()
        failures_key = self._key(provider, "failures")
        circuit_key = self._key(provider, "circuit_until")
        try:
            failures = int(client.incr(failures_key))
            client.expire(failures_key, 300)
            if failures >= 5:
                client.set(circuit_key, time.time() + 300, ex=300)
        except RedisError as exc:
            raise AITransientError("AI Redis circuit update unavailable") from exc

    def reset(self, provider: str) -> None:
        if self._should_use_local_fallback():
            self._local.reset(provider)
            return
        client = self._client()
        pattern = f"{self.key_prefix}:{provider}:*"
        try:
            for key in client.scan_iter(match=pattern, count=100):
                client.delete(key)
            for key in client.scan_iter(match=f"{self.key_prefix}:dedupe:{provider}:*", count=100):
                client.delete(key)
        except RedisError as exc:
            raise AITransientError("AI Redis limiter reset unavailable") from exc

    def _acquire_redis(
        self,
        provider: str,
        *,
        max_concurrency: int,
        requests_per_minute: int,
        daily_request_budget: int,
        daily_token_budget: int,
    ) -> AILimitLease:
        client = self._client()
        active_key = self._key(provider, "active")
        minute_key = self._key(provider, f"minute:{int(time.time() // 60)}")
        daily_requests_key = self._daily_key(provider, "requests")
        daily_tokens_key = self._daily_key(provider, "tokens")
        circuit_key = self._key(provider, "circuit_until")
        script = """
        local now = tonumber(ARGV[1])
        local max_concurrency = tonumber(ARGV[2])
        local rpm = tonumber(ARGV[3])
        local active_ttl = tonumber(ARGV[4])
        local minute_ttl = tonumber(ARGV[5])
        local daily_request_budget = tonumber(ARGV[6])
        local daily_token_budget = tonumber(ARGV[7])
        local daily_ttl = tonumber(ARGV[8])
        local circuit_until = tonumber(redis.call('GET', KEYS[5]) or '0')
        if circuit_until > now then
            return {0, 'circuit', math.ceil(circuit_until - now)}
        end
        local current_tokens = tonumber(redis.call('GET', KEYS[4]) or '0')
        if daily_token_budget > 0 and current_tokens >= daily_token_budget then
            return {0, 'daily_token', daily_ttl}
        end
        local active = tonumber(redis.call('INCR', KEYS[1]))
        redis.call('EXPIRE', KEYS[1], active_ttl)
        if active > max_concurrency then
            redis.call('DECR', KEYS[1])
            return {0, 'concurrency', 1}
        end
        local minute = tonumber(redis.call('INCR', KEYS[2]))
        if minute == 1 then redis.call('EXPIRE', KEYS[2], minute_ttl) end
        if minute > rpm then
            redis.call('DECR', KEYS[1])
            return {0, 'minute', redis.call('TTL', KEYS[2])}
        end
        if daily_request_budget > 0 then
            local requests = tonumber(redis.call('INCR', KEYS[3]))
            if requests == 1 then redis.call('EXPIRE', KEYS[3], daily_ttl) end
            if requests > daily_request_budget then
                redis.call('DECR', KEYS[1])
                redis.call('DECR', KEYS[3])
                return {0, 'daily_request', daily_ttl}
            end
        end
        return {1, 'ok', 0}
        """
        try:
            result = cast(
                list[Any],
                client.eval(
                    script,
                    5,
                    active_key,
                    minute_key,
                    daily_requests_key,
                    daily_tokens_key,
                    circuit_key,
                    time.time(),
                    max(1, int(max_concurrency)),
                    max(1, int(requests_per_minute)),
                    300,
                    70,
                    max(0, int(daily_request_budget)),
                    max(0, int(daily_token_budget)),
                    _seconds_until_tomorrow(),
                ),
            )
        except RedisError as exc:
            raise AITransientError("AI Redis limiter unavailable") from exc
        allowed, reason, retry_after = result
        if int(allowed) == 1:
            return AILimitLease(provider=provider, request_key=active_key, token="")
        if reason == "daily_request":
            raise AIBudgetExceededError("AI daily request budget exceeded")
        if reason == "daily_token":
            raise AIBudgetExceededError("AI daily token budget exceeded")
        if reason == "circuit":
            raise AICircuitOpenError("AI provider circuit breaker is open")
        raise AIRateLimitedError(retry_after_seconds=max(1, int(retry_after or 1)))

    def _release_redis(self, lease: AILimitLease) -> None:
        client = self._client()
        script = """
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        if current <= 1 then
            redis.call('DEL', KEYS[1])
            return 0
        end
        return redis.call('DECR', KEYS[1])
        """
        try:
            client.eval(script, 1, lease.request_key)
        except RedisError as exc:
            raise AITransientError("AI Redis limiter release unavailable") from exc

    def _release_lock(self, client: redis.Redis, key: str, token: str) -> None:
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        try:
            client.eval(script, 1, key, token)
        except RedisError as exc:
            raise AITransientError("AI Redis dedupe release unavailable") from exc

    def _client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redis

    def _should_use_local_fallback(self) -> bool:
        explicit_redis = os.environ.get("REDIS_URL") or os.environ.get("TEST_REDIS_URL")
        return not explicit_redis and settings.app_env in {"test", "local"}

    def _key(self, provider: str, suffix: str) -> str:
        return f"{self.key_prefix}:{provider}:{suffix}"

    def _daily_key(self, provider: str, suffix: str) -> str:
        day = datetime.now(UTC).strftime("%Y%m%d")
        return self._key(provider, f"daily:{day}:{suffix}")


class _LocalLimitController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, int] = {}
        self._minute: dict[str, tuple[int, float]] = {}
        self._requests: dict[str, int] = {}
        self._tokens: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._circuit_until: dict[str, float] = {}
        self._dedupe: set[str] = set()

    @contextmanager
    def reserve(
        self,
        provider: str,
        *,
        max_concurrency: int,
        requests_per_minute: int,
        daily_request_budget: int,
        daily_token_budget: int,
    ) -> Iterator[None]:
        self._acquire(
            provider,
            max_concurrency,
            requests_per_minute,
            daily_request_budget,
            daily_token_budget,
        )
        try:
            yield
        finally:
            with self._lock:
                self._active[provider] = max(self._active.get(provider, 1) - 1, 0)

    @contextmanager
    def input_hash_lock(self, lock_key: str, *, ttl_seconds: int) -> Iterator[None]:
        del ttl_seconds
        with self._lock:
            if lock_key in self._dedupe:
                raise AIRateLimitedError(retry_after_seconds=1)
            self._dedupe.add(lock_key)
        try:
            yield
        finally:
            with self._lock:
                self._dedupe.discard(lock_key)

    def sync_daily_usage(self, provider: str, usage: AIUsageSnapshot) -> None:
        with self._lock:
            self._tokens[provider] = max(self._tokens.get(provider, 0), usage.tokens_today)
            self._requests[provider] = max(self._requests.get(provider, 0), usage.requests_today)

    def record_token_usage(self, provider: str, tokens: int) -> None:
        with self._lock:
            self._tokens[provider] = self._tokens.get(provider, 0) + tokens

    def record_success(self, provider: str) -> None:
        with self._lock:
            self._failures[provider] = 0
            self._circuit_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        with self._lock:
            failures = self._failures.get(provider, 0) + 1
            self._failures[provider] = failures
            if failures >= 5:
                self._circuit_until[provider] = time.monotonic() + 300

    def reset(self, provider: str) -> None:
        with self._lock:
            self._active.pop(provider, None)
            self._minute.pop(provider, None)
            self._requests.pop(provider, None)
            self._tokens.pop(provider, None)
            self._failures.pop(provider, None)
            self._circuit_until.pop(provider, None)
            self._dedupe = {item for item in self._dedupe if not item.startswith(f"{provider}:")}

    def _acquire(
        self,
        provider: str,
        max_concurrency: int,
        requests_per_minute: int,
        daily_request_budget: int,
        daily_token_budget: int,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if self._circuit_until.get(provider, 0) > now:
                raise AICircuitOpenError("AI provider circuit breaker is open")
            if daily_token_budget > 0 and self._tokens.get(provider, 0) >= daily_token_budget:
                raise AIBudgetExceededError("AI daily token budget exceeded")
            if self._active.get(provider, 0) >= max_concurrency:
                raise AIRateLimitedError(retry_after_seconds=1)
            count, window_start = self._minute.get(provider, (0, now))
            if now - window_start >= 60:
                count = 0
                window_start = now
            if count >= requests_per_minute:
                raise AIRateLimitedError(retry_after_seconds=max(1, 60 - int(now - window_start)))
            if daily_request_budget > 0 and self._requests.get(provider, 0) >= daily_request_budget:
                raise AIBudgetExceededError("AI daily request budget exceeded")
            self._active[provider] = self._active.get(provider, 0) + 1
            self._minute[provider] = (count + 1, window_start)
            if daily_request_budget > 0:
                self._requests[provider] = self._requests.get(provider, 0) + 1


def _seconds_until_tomorrow() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


ai_limit_controller = AILimitController()
