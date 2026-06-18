from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.errors import (
    AccessDeniedError,
    FetchError,
    InvalidContentTypeError,
    ResponseTooLargeError,
    RobotsDisallowedError,
)
from app.core.time import utc_now
from app.core.url_security import normalize_redirect_url, redact_url, validate_public_http_url
from app.fetch.rate_limit import HostRateLimiter
from app.fetch.retry import TRANSIENT_STATUS_CODES, exponential_backoff, parse_retry_after
from app.fetch.robots import RobotsCache
from app.fetch.user_agent import default_headers
from app.observability.metrics import (
    fetch_attempts_total,
    fetch_duration_seconds,
    fetch_results_total,
    fetch_retries_total,
    status_group,
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class FetchResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str
    content_type: str | None
    body_hash: str
    fetched_at: Any


class FetchClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float | None = None,
        max_response_bytes: int | None = None,
        rate_limiter: HostRateLimiter | None = None,
        robots_cache: RobotsCache | None = None,
        max_retries: int | None = None,
        max_redirects: int | None = None,
        allow_private_networks: bool | None = None,
        allow_localhost: bool | None = None,
        validate_dns_rebinding: bool | None = None,
        backoff_base_seconds: float = 0.5,
    ):
        self.timeout_seconds = timeout_seconds or settings.http_timeout_seconds
        self.max_response_bytes = max_response_bytes or settings.http_max_response_bytes
        self.max_retries = settings.http_max_retries if max_retries is None else max_retries
        self.max_redirects = settings.http_max_redirects if max_redirects is None else max_redirects
        self.allow_private_networks = (
            settings.allow_private_networks
            if allow_private_networks is None
            else allow_private_networks
        )
        self.allow_localhost = (
            settings.http_allow_localhost if allow_localhost is None else allow_localhost
        )
        self.validate_dns_rebinding = (
            settings.http_validate_dns_rebinding
            if validate_dns_rebinding is None
            else validate_dns_rebinding
        )
        self.backoff_base_seconds = backoff_base_seconds
        self.rate_limiter = rate_limiter or HostRateLimiter(
            settings.http_per_host_rate_limit_seconds
        )
        self.robots_cache = robots_cache or RobotsCache(
            user_agent=settings.http_user_agent, timeout_seconds=self.timeout_seconds
        )
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            headers=default_headers(),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def __aenter__(self) -> FetchClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        respect_robots: bool = False,
        allowed_content_types: tuple[str, ...] | None = None,
    ) -> FetchResponse:
        if respect_robots:
            allowed = await self.robots_cache.allowed(url, client=self.client)
            if not allowed:
                raise RobotsDisallowedError(url)
        return await self._request(
            "GET", url, headers=headers, allowed_content_types=allowed_content_types
        )

    async def post_json(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
        allowed_content_types: tuple[str, ...] | None = None,
    ) -> FetchResponse:
        merged_headers = {"Content-Type": "application/json", **(headers or {})}
        return await self._request(
            "POST",
            url,
            json=json,
            headers=merged_headers,
            allowed_content_types=allowed_content_types,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        allowed_content_types: tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> FetchResponse:
        last_error: Exception | None = None
        current_url = url
        redirects_seen = 0
        validate_public_http_url(
            current_url,
            allow_private_networks=self.allow_private_networks,
            allow_localhost=self.allow_localhost,
            resolve_dns=self.validate_dns_rebinding,
        )
        started = time.perf_counter()
        fetch_attempts_total.labels(method=method).inc()
        for attempt in range(1, self.max_retries + 2):
            try:
                while True:
                    await self.rate_limiter.wait(current_url)
                    response = await self.client.request(method, current_url, **kwargs)
                    self._raise_for_terminal_status(response)
                    self._raise_for_size(response)
                    if not response.is_redirect:
                        break
                    redirects_seen += 1
                    if redirects_seen > self.max_redirects:
                        raise FetchError(
                            "maximum redirect count exceeded", error_code="redirect_loop"
                        )
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError(
                            "redirect missing Location header", error_code="bad_redirect"
                        )
                    current_url = normalize_redirect_url(str(response.url), location)
                    validate_public_http_url(
                        current_url,
                        allow_private_networks=self.allow_private_networks,
                        allow_localhost=self.allow_localhost,
                        resolve_dns=self.validate_dns_rebinding,
                    )
                if response.status_code in TRANSIENT_STATUS_CODES and attempt <= self.max_retries:
                    delay = parse_retry_after(response.headers.get("Retry-After"))
                    if delay is None:
                        delay = exponential_backoff(attempt, base_seconds=self.backoff_base_seconds)
                    logger.info(
                        "fetch.retry",
                        url=redact_url(current_url),
                        method=method,
                        status_code=response.status_code,
                        attempt=attempt,
                        delay_seconds=delay,
                    )
                    fetch_retries_total.labels(method=method, reason="http_status").inc()
                    await asyncio.sleep(delay)
                    continue
                if response.status_code >= 400:
                    raise FetchError(
                        f"HTTP {response.status_code} while fetching {redact_url(current_url)}",
                        status_code=response.status_code,
                        error_code="http_error",
                    )
                self._raise_for_content_type(response, allowed_content_types)
                fetch_results_total.labels(
                    method=method,
                    outcome="success",
                    status_group=status_group(response.status_code),
                ).inc()
                fetch_duration_seconds.labels(method=method, outcome="success").observe(
                    time.perf_counter() - started
                )
                return self._to_fetch_response(response)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    break
                delay = exponential_backoff(attempt, base_seconds=self.backoff_base_seconds)
                logger.info(
                    "fetch.transport_retry",
                    url=redact_url(current_url),
                    method=method,
                    attempt=attempt,
                    delay_seconds=delay,
                    error=str(exc),
                )
                fetch_retries_total.labels(method=method, reason="transport").inc()
                await asyncio.sleep(delay)
        fetch_results_total.labels(method=method, outcome="failure", status_group="none").inc()
        fetch_duration_seconds.labels(method=method, outcome="failure").observe(
            time.perf_counter() - started
        )
        raise FetchError(
            f"failed to fetch {redact_url(current_url)}: {last_error}",
            error_code="transport_error",
        )

    def _raise_for_terminal_status(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AccessDeniedError(status_code=response.status_code)

    def _raise_for_size(self, response: httpx.Response) -> None:
        content_length = response.headers.get("Content-Length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self.max_response_bytes
        ):
            raise ResponseTooLargeError(self.max_response_bytes)
        if len(response.content) > self.max_response_bytes:
            raise ResponseTooLargeError(self.max_response_bytes)

    def _raise_for_content_type(
        self,
        response: httpx.Response,
        allowed_content_types: tuple[str, ...] | None,
    ) -> None:
        if not allowed_content_types:
            return
        content_type = response.headers.get("content-type")
        if not content_type:
            return
        normalized = content_type.split(";", 1)[0].strip().lower()
        if not any(normalized == allowed.lower() for allowed in allowed_content_types):
            raise InvalidContentTypeError(content_type)

    def _to_fetch_response(self, response: httpx.Response) -> FetchResponse:
        body = response.text
        return FetchResponse(
            url=str(response.url),
            status_code=response.status_code,
            headers={key: value for key, value in response.headers.items()},
            text=body,
            content_type=response.headers.get("content-type"),
            body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            fetched_at=utc_now(),
        )
